"""Fast unit: compare local row lookup with SQLite's real row implementation.

Purpose: avoid wrong cells when aliases differ only in ASCII case.
Expected1s/max5s; this is no deployment or provider proof.
"""
import sqlite3
import unittest
from notification_center.mesh_store import Row


class RowContractTests(unittest.TestCase):
    def test_first_case_insensitive_alias_matches_sqlite(self):
        database=sqlite3.connect(':memory:')
        database.row_factory=sqlite3.Row
        cursor=database.execute('SELECT 1 AS id,2 AS ID,3 AS "Ä"')
        oracle=cursor.fetchone()
        actual=Row([item[0] for item in cursor.description],[1,2,3])
        for key in (0,1,'id','ID','Id','Ä'):
            self.assertEqual(actual[key],oracle[key])
        for row in (oracle,actual):
            with self.assertRaises(IndexError):row['ä']
        database.close()
