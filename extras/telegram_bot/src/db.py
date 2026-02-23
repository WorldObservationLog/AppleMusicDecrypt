import asyncio
import json
import os
from typing import Dict, Any, Optional

CURRENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(CURRENT_DIR, "users.json")


import asyncio
import json
import os
import sqlite3
from typing import Dict, Any, Optional

CURRENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEGACY_DB_PATH = os.path.join(CURRENT_DIR, "users.json")
DB_PATH = os.path.join(CURRENT_DIR, "bot_database.db")


class UserDB:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self.lock = asyncio.Lock()
        
        # In-memory fast checks
        self.data: Dict[str, Any] = {"whitelist": [], "blacklist": []}

    def _sync_init_db(self):
        with sqlite3.connect(self.path) as conn:
            cursor = conn.cursor()
            # users table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    settings TEXT
                )
            """)
            # lists table (for whitelist/blacklist)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS lists (
                    user_id INTEGER,
                    list_type TEXT,
                    PRIMARY KEY (user_id, list_type)
                )
            """)
            # cache table (flattening adam_id, codec, language)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    adam_id TEXT,
                    codec TEXT,
                    language TEXT,
                    file_id TEXT,
                    UNIQUE(file_id)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cache_adam ON cache(adam_id)")
            conn.commit()
            
            # Load lists to memory
            cursor.execute("SELECT user_id, list_type FROM lists")
            rows = cursor.fetchall()
            for uid, ltype in rows:
                if ltype in ("whitelist", "blacklist"):
                    self.data[ltype].append(uid)
                    
            # Migrate legacy JSON if needed
            if os.path.exists(LEGACY_DB_PATH):
                self._sync_migrate_legacy(conn, cursor)

    def _sync_migrate_legacy(self, conn, cursor):
        try:
            with open(LEGACY_DB_PATH, "r", encoding="utf-8") as f:
                legacy_data = json.load(f)
            
            # Migrate users
            if "users" in legacy_data:
                for uid, settings in legacy_data["users"].items():
                    cursor.execute("INSERT OR IGNORE INTO users (user_id, settings) VALUES (?, ?)", 
                                   (uid, json.dumps(settings)))
            
            # Migrate lists
            for ltype in ("whitelist", "blacklist"):
                if ltype in legacy_data:
                    for uid in legacy_data[ltype]:
                        cursor.execute("INSERT OR IGNORE INTO lists (user_id, list_type) VALUES (?, ?)", 
                                       (uid, ltype))
                        if uid not in self.data[ltype]:
                            self.data[ltype].append(uid)
                            
            # Migrate cache
            if "cache" in legacy_data:
                for adam_id, entries in legacy_data["cache"].items():
                    for entry in entries:
                        cursor.execute("""
                            INSERT OR IGNORE INTO cache (adam_id, codec, language, file_id) 
                            VALUES (?, ?, ?, ?)
                        """, (adam_id, entry.get("codec"), entry.get("language"), entry.get("file_id")))
                        
            conn.commit()
            os.rename(LEGACY_DB_PATH, LEGACY_DB_PATH + ".migrated")
            print("Successfully migrated users.json to sqlite.")
        except Exception as e:
            print(f"Failed to migrate legacy db: {e}")

    async def load_initial(self):
        await asyncio.to_thread(self._sync_init_db)

    def _sync_get_settings(self, uid: str) -> Dict[str, Any]:
        with sqlite3.connect(self.path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT settings FROM users WHERE user_id = ?", (uid,))
            row = cursor.fetchone()
            if row:
                return json.loads(row[0])
            else:
                cursor.execute("INSERT INTO users (user_id, settings) VALUES (?, ?)", (uid, "{}"))
                conn.commit()
                return {}

    async def get_user_settings(self, user_id: int) -> Dict[str, Any]:
        return await asyncio.to_thread(self._sync_get_settings, str(user_id))

    def _sync_update_settings(self, uid: str, new_settings: Dict[str, Any]):
        with sqlite3.connect(self.path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT settings FROM users WHERE user_id = ?", (uid,))
            row = cursor.fetchone()
            if row:
                settings = json.loads(row[0])
            else:
                settings = {}
            settings.update(new_settings)
            cursor.execute("INSERT OR REPLACE INTO users (user_id, settings) VALUES (?, ?)", 
                           (uid, json.dumps(settings)))
            conn.commit()

    async def update_user_settings(self, user_id: int, settings: Dict[str, Any]):
        await asyncio.to_thread(self._sync_update_settings, str(user_id), settings)

    def is_whitelisted(self, user_id: int) -> bool:
        return user_id in self.data["whitelist"]

    def _sync_add_list(self, user_id: int, list_type: str):
        with sqlite3.connect(self.path) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO lists (user_id, list_type) VALUES (?, ?)", (user_id, list_type))
            conn.commit()

    async def add_whitelist(self, user_id: int):
        if user_id not in self.data["whitelist"]:
            self.data["whitelist"].append(user_id)
            await asyncio.to_thread(self._sync_add_list, user_id, "whitelist")

    def _sync_remove_list(self, user_id: int, list_type: str):
        with sqlite3.connect(self.path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM lists WHERE user_id = ? AND list_type = ?", (user_id, list_type))
            conn.commit()

    async def remove_whitelist(self, user_id: int):
        if user_id in self.data["whitelist"]:
            self.data["whitelist"].remove(user_id)
            await asyncio.to_thread(self._sync_remove_list, user_id, "whitelist")

    def is_blacklisted(self, user_id: int) -> bool:
        return user_id in self.data["blacklist"]

    async def add_blacklist(self, user_id: int):
        if user_id not in self.data["blacklist"]:
            self.data["blacklist"].append(user_id)
            await asyncio.to_thread(self._sync_add_list, user_id, "blacklist")

    async def remove_blacklist(self, user_id: int):
        if user_id in self.data["blacklist"]:
            self.data["blacklist"].remove(user_id)
            await asyncio.to_thread(self._sync_remove_list, user_id, "blacklist")

    def _sync_get_cache(self, adam_id: str, codec: str, language: str, loose: bool) -> Optional[str]:
        with sqlite3.connect(self.path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT file_id, codec, language FROM cache WHERE adam_id = ?", (adam_id,))
            rows = cursor.fetchall()
            for fid, c, lang in rows:
                if c == codec:
                    if loose or lang == language:
                        return fid
            return None

    async def get_cache(self, adam_id: str, codec: str, language: str, loose: bool) -> Optional[str]:
        return await asyncio.to_thread(self._sync_get_cache, adam_id, codec, language, loose)

    def _sync_set_cache(self, adam_id: str, codec: str, language: str, file_id: str):
        with sqlite3.connect(self.path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO cache (adam_id, codec, language, file_id) 
                VALUES (?, ?, ?, ?)
            """, (adam_id, codec, language, file_id))
            conn.commit()

    async def set_cache(self, adam_id: str, codec: str, language: str, file_id: str):
        await asyncio.to_thread(self._sync_set_cache, adam_id, codec, language, file_id)


user_db = UserDB()
