// NoticePlace's private SQL adapter. Dqlite owns replication and leader election;
// one pinned SQL connection preserves transactions across local IPC requests.
package main

import (
	"bufio"
	"context"
	"crypto/tls"
	"crypto/x509"
	"database/sql"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/canonical/go-dqlite/v2/app"
)

const maxFrame = 16 * 1024 * 1024

type request struct {
	Op         string        `json:"op"`
	SQL        string        `json:"sql"`
	Params     []interface{} `json:"params"`
	Statements []request     `json:"statements,omitempty"`
}
type response struct {
	Columns    []string        `json:"columns,omitempty"`
	Rows       [][]interface{} `json:"rows,omitempty"`
	RowCount   int64           `json:"rowcount"`
	LastID     int64           `json:"lastrowid,omitempty"`
	Error      string          `json:"error,omitempty"`
	ErrorClass string          `json:"error_class,omitempty"`
	Cluster    interface{}     `json:"cluster,omitempty"`
	Leader     string          `json:"leader,omitempty"`
}

func sqlParams(values []interface{}) ([]interface{}, error) {
	result := make([]interface{}, len(values))
	for i, v := range values {
		if n, ok := v.(json.Number); ok {
			if integer, e := n.Int64(); e == nil {
				result[i] = integer
			} else {
				number, e := n.Float64()
				if e != nil {
					return nil, e
				}
				result[i] = number
			}
		} else {
			result[i] = v
		}
	}
	return result, nil
}

func execute(ctx context.Context, conn *sql.Conn, req request) response {
	if req.Op == "batch" {
		if len(req.Statements) > 2049 {
			return failed(errors.New("bounded SQL batch exceeded"))
		}
		if _, e := conn.ExecContext(ctx, "BEGIN IMMEDIATE"); e != nil {
			return failed(e)
		}
		count := int64(0)
		for _, statement := range req.Statements {
			if statement.Op != "exec" {
				conn.ExecContext(ctx, "ROLLBACK")
				return failed(errors.New("batch contains a non-write operation"))
			}
			result := execute(ctx, conn, statement)
			if result.Error != "" {
				conn.ExecContext(ctx, "ROLLBACK")
				return result
			}
			count += result.RowCount
		}
		if _, e := conn.ExecContext(ctx, "COMMIT"); e != nil {
			return failed(e)
		}
		return response{RowCount: count}
	}
	params, e := sqlParams(req.Params)
	if e != nil {
		return failed(e)
	}
	if req.Op == "query" {
		rows, e := conn.QueryContext(ctx, req.SQL, params...)
		if e != nil {
			return failed(e)
		}
		defer rows.Close()
		columns, e := rows.Columns()
		if e != nil {
			return failed(e)
		}
		result := response{Columns: columns, Rows: [][]interface{}{}, RowCount: -1}
		size := 0
		for rows.Next() {
			values := make([]interface{}, len(columns))
			pointers := make([]interface{}, len(columns))
			for i := range values {
				pointers[i] = &values[i]
			}
			if e := rows.Scan(pointers...); e != nil {
				return failed(e)
			}
			for i, v := range values {
				if b, ok := v.([]byte); ok {
					values[i] = string(b)
				}
			}
			encoded, e := json.Marshal(values)
			if e != nil {
				return failed(e)
			}
			size += len(encoded)
			if size > maxFrame-4096 {
				return failed(errors.New("bounded SQL result exceeded"))
			}
			result.Rows = append(result.Rows, values)
		}
		if e := rows.Err(); e != nil {
			return failed(e)
		}
		return result
	}
	if req.Op != "exec" {
		return failed(errors.New("unsupported SQL operation"))
	}
	result, e := conn.ExecContext(ctx, req.SQL, params...)
	if e != nil {
		return failed(e)
	}
	count, e := result.RowsAffected()
	if e != nil {
		return failed(e)
	}
	id, _ := result.LastInsertId()
	return response{RowCount: count, LastID: id}
}

func failed(e error) response {
	kind := "operational"
	// The Python core catches SQLite constraint violations explicitly.
	if strings.Contains(strings.ToLower(e.Error()), "constraint") {
		kind = "integrity"
	}
	return response{Error: e.Error(), ErrorClass: kind, RowCount: -1}
}

func serve(local net.Conn, db *sql.DB, node *app.App) {
	defer local.Close()
	var conn *sql.Conn
	defer func() {
		if conn != nil {
			conn.Close()
		}
	}()
	scanner := bufio.NewScanner(local)
	scanner.Buffer(make([]byte, 65536), maxFrame)
	output := json.NewEncoder(local)
	for {
		local.SetReadDeadline(time.Now().Add(120 * time.Second))
		if !scanner.Scan() {
			return
		}
		var req request
		decoder := json.NewDecoder(strings.NewReader(scanner.Text()))
		decoder.UseNumber()
		if e := decoder.Decode(&req); e != nil {
			output.Encode(failed(e))
			return
		}
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		var result response
		if req.Op == "cluster" {
			leader, e := node.FindLeader(ctx)
			if e != nil {
				result = failed(e)
			} else {
				members, e := leader.Cluster(ctx)
				if e != nil {
					result = failed(e)
				} else {
					result.Cluster = members
				}
				info, e := leader.Leader(ctx)
				if e == nil && info != nil {
					result.Leader = info.Address
				}
				leader.Close()
			}
		} else {
			if conn == nil {
				var e error
				conn, e = db.Conn(ctx)
				if e != nil {
					result = failed(e)
				}
			}
			if conn != nil {
				result = execute(ctx, conn, req)
			}
		}
		cancel()
		local.SetWriteDeadline(time.Now().Add(10 * time.Second))
		if e := output.Encode(result); e != nil {
			return
		}
		// Never silently retry a failed query/transaction. The caller reconciles
		// committed/unknown business state before issuing a new logical operation.
		if result.Error != "" && result.ErrorClass != "integrity" {
			return
		}
	}
}

func main() {
	if !registerVoiceCasefold() {
		log.Fatal("voice SQLite function registration failed")
	}
	state := flag.String("state", "", "durable state directory")
	address := flag.String("address", "", "private cluster address")
	join := flag.String("join", "", "comma-separated existing cluster addresses; empty on bootstrap only")
	socketPath := flag.String("socket", "", "private local SQL socket")
	certificate := flag.String("cert", "", "cluster certificate")
	key := flag.String("key", "", "cluster private key")
	caPath := flag.String("ca", "", "cluster CA")
	flag.Parse()
	if !filepath.IsAbs(*state) || !filepath.IsAbs(*socketPath) || *address == "" {
		log.Fatal("absolute state/socket and private address required")
	}
	cert, e := tls.LoadX509KeyPair(*certificate, *key)
	if e != nil {
		log.Fatal(e)
	}
	caBytes, e := os.ReadFile(*caPath)
	if e != nil {
		log.Fatal(e)
	}
	ca := x509.NewCertPool()
	if !ca.AppendCertsFromPEM(caBytes) {
		log.Fatal("invalid cluster CA")
	}
	listenTLS := &tls.Config{Certificates: []tls.Certificate{cert}, ClientCAs: ca, ClientAuth: tls.RequireAndVerifyClientCert, MinVersion: tls.VersionTLS12}
	dialTLS := &tls.Config{Certificates: []tls.Certificate{cert}, RootCAs: ca, ServerName: "noticeplace-mesh", MinVersion: tls.VersionTLS12}
	options := []app.Option{app.WithAddress(*address), app.WithTLS(listenTLS, dialTLS), app.WithVoters(3), app.WithNetworkLatency(10 * time.Millisecond)}
	if *join != "" {
		options = append(options, app.WithCluster(strings.Split(*join, ",")))
	}
	node, e := app.New(*state, options...)
	if e != nil {
		log.Fatal(e)
	}
	defer node.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	db, e := node.Open(ctx, "noticeplace")
	cancel()
	if e != nil {
		log.Fatal(e)
	}
	defer db.Close()
	db.SetMaxOpenConns(8)
	db.SetMaxIdleConns(0)
	if _, e := os.Lstat(*socketPath); !os.IsNotExist(e) {
		log.Fatal("existing local socket requires owner reconciliation")
	}
	listener, e := net.Listen("unix", *socketPath)
	if e != nil {
		log.Fatal(e)
	}
	if e := os.Chmod(*socketPath, 0600); e != nil {
		log.Fatal(e)
	}
	defer os.Remove(*socketPath)
	done := make(chan os.Signal, 1)
	signal.Notify(done, syscall.SIGINT, syscall.SIGTERM)
	go func() { <-done; listener.Close() }()
	slots := make(chan struct{}, 8)
	for {
		conn, e := listener.Accept()
		if e != nil {
			if errors.Is(e, net.ErrClosed) {
				break
			}
			log.Print(e)
			continue
		}
		select {
		case slots <- struct{}{}:
			go func() { defer func() { <-slots }(); serve(conn, db, node) }()
		default:
			io.WriteString(conn, "{\"error\":\"bounded SQL connection capacity\",\"error_class\":\"operational\"}\n")
			conn.Close()
		}
	}
	fmt.Println("NoticePlace mesh store stopped")
}
