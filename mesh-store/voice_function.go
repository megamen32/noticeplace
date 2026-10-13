package main

/*
#include <stdlib.h>
#include "voice_function.h"
*/
import "C"

import (
	"golang.org/x/text/cases"
	"unsafe"
)

//export noticeVoiceCasefold
func noticeVoiceCasefold(context *C.sqlite3_context, count C.int, values **C.sqlite3_value) {
	text := ""
	if count == 1 && values != nil {
		raw := C.sqlite3_value_text(*values)
		if raw != nil {
			text = string(C.GoBytes(unsafe.Pointer(raw), C.sqlite3_value_bytes(*values)))
		}
	}
	folded := cases.Fold().String(text)
	result := C.CString(folded)
	defer C.free(unsafe.Pointer(result))
	C.voiceResult(context, result, C.int(len(folded)))
}

func registerVoiceCasefold() bool { return C.registerVoiceFunction() == C.SQLITE_OK }
