#define SQLITE_CORE
#include <stddef.h>
#include <sqlite3ext.h>
#include "voice_function.h"

extern void noticeVoiceCasefold(sqlite3_context *, int, sqlite3_value **);

static int initializeVoice(sqlite3 *db, char **error,
                           const sqlite3_api_routines *api) {
    (void)error;
    (void)api;
    return sqlite3_create_function_v2(db, "voice_casefold", 1,
        SQLITE_UTF8 | SQLITE_DETERMINISTIC, NULL,
        noticeVoiceCasefold, NULL, NULL, NULL);
}

int registerVoiceFunction(void) {
    return sqlite3_auto_extension((void (*)(void))initializeVoice);
}

void voiceResult(sqlite3_context *context, char *text, int size) {
    sqlite3_result_text(context, text, size, SQLITE_TRANSIENT);
}
