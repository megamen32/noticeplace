#include <sqlite3.h>
int registerVoiceFunction(void);
void voiceResult(sqlite3_context *context, char *text, int size);
