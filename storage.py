from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


@dataclass(frozen=True)
class GmailOAuthCredentials:
    gmail_address: str
    refresh_token: str


class CredentialStore:
    def __init__(self, *, db_path: str, encryption_key: str):
        self._db_path = db_path
        self._fernet = Fernet(encryption_key.encode("utf-8"))
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._db_path)
        con.execute("PRAGMA journal_mode=WAL;")
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS gmail_oauth_credentials (
                    telegram_user_id INTEGER PRIMARY KEY,
                    gmail_address TEXT NOT NULL,
                    refresh_token_enc BLOB NOT NULL
                )
                """
            )

    def set_gmail_oauth_credentials(self, *, telegram_user_id: int, gmail_address: str, refresh_token: str) -> None:
        refresh_token_enc = self._fernet.encrypt(refresh_token.encode("utf-8"))
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO gmail_oauth_credentials (telegram_user_id, gmail_address, refresh_token_enc)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    gmail_address=excluded.gmail_address,
                    refresh_token_enc=excluded.refresh_token_enc
                """,
                (telegram_user_id, gmail_address, refresh_token_enc),
            )

    def get_gmail_oauth_credentials(self, *, telegram_user_id: int) -> Optional[GmailOAuthCredentials]:
        with self._connect() as con:
            row = con.execute(
                "SELECT gmail_address, refresh_token_enc FROM gmail_oauth_credentials WHERE telegram_user_id=?",
                (telegram_user_id,),
            ).fetchone()
        if not row:
            return None
        gmail_address, refresh_token_enc = row
        try:
            refresh_token = self._fernet.decrypt(refresh_token_enc).decode("utf-8")
        except InvalidToken:
            # Encryption key changed; stored secrets are no longer decryptable.
            return None
        return GmailOAuthCredentials(gmail_address=gmail_address, refresh_token=refresh_token)

    def delete_gmail_oauth_credentials(self, *, telegram_user_id: int) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM gmail_oauth_credentials WHERE telegram_user_id=?", (telegram_user_id,))
