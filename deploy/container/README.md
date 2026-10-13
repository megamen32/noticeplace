# NoticePlace API container pilot

The image runs the existing NoticePlace event intake and authenticated reads against its business store. It has
no delivery threads, Telegram poller, voice subscriber or native agent
executor; its HTTP MCP dispatcher is disabled. Existing mesh workers retain external-delivery ownership. Dqlite,
the shared Rust mesh core, ingress and phone gateway remain outside the
container. This is an API pilot, not a complete containerized deployment.

Build from a published source archive with the pinned Python base:

```sh
docker build -f deploy/container/Dockerfile \
  --build-arg SOURCE_REVISION="$noticeplace_source_revision" \
  -t "noticeplace-api:$noticeplace_source_revision" .
```

Use a reviewed immutable image identity in `NOTICEPLACE_API_IMAGE`, set
`NOTICEPLACE_UID` and `NOTICEPLACE_GID` to the actual host socket owner, and
set `NOTICEPLACE_API_ENV` to a root-owned0600 file. That file contains only
producer scopes and these application settings:

- `NOTIFY_CENTER_TOKENS_JSON`: existing approved producer authorization;
- `NOTIFY_CENTER_HEALTH_TOKEN`: protected health endpoint authorization;
- `NOTIFY_CENTER_DB=mesh:/run/noticeplace-mesh/sql.sock`;
- `NOTICEPLACE_MESH_CORE_SOCKET=/run/noticeplace-mesh-core/core.sock`;
- `NOTICEPLACE_MESH_HOST_ID`: the actual owning host;
- `NOTICEPLACE_MESH_HOSTS_JSON`: the approved complete mesh host set.

It needs no Telegram, telephone or provider credential. Start with an
independent Compose project name; the default pilot listener is loopback18095:

```sh
docker compose -p noticeplace-api-pilot -f deploy/container/compose.yaml up -d
```

Readiness checks both the actual HTTP endpoint and an actual quorum SQL
query. Consumer acceptance additionally requires an authenticated harmless
write, readback from a separate native replica, preservation across an owned
container restart and an unauthorized401 response. A healthy container is
insufficient release evidence.

Only `/v1/events` accepts POST, including canonical event resolution. Human
question mutations, immediate native completion delivery and HTTP MCP jobs
are disabled before reading their payload or changing shared state. The
normal native API keeps its existing complete route set.

The same image and configuration are intended for a later Kubernetes pilot.
Its pod must receive the two real socket mounts and matching host identity;
moving the pod to another host requires that host's actual mesh sidecars and
socket UID/GID. No Kubernetes API is needed by the application.

Rollback stops only this Compose project, leaving native replicas, shared
state and delivery workers running. Do not automatically run a second
delivery owner when Kubernetes becomes unreachable. Remove only owned
containers/networks; preserve image identity and acceptance receipts.
