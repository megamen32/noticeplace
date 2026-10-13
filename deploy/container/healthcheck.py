"""Check the real API and real quorum SQL path without creating an event."""
import json
import os
import urllib.request
from notification_center.mesh_store import connect

url='http://127.0.0.1:'+os.environ.get('NOTIFY_CENTER_PORT','8091')+'/v1/instructions'
with urllib.request.urlopen(url,timeout=2) as response:
    assert response.status==200
    json.load(response)
database=connect(os.environ['NOTIFY_CENTER_DB'],readonly=True)
try:
    assert database.execute('SELECT 1').fetchone()[0]==1
finally:
    database.close()
