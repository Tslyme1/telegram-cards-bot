import sqlite3
from contextlib import closing

DB_PATH = "cards.db"


def init_db(path: str = DB_PATH) -> None:
    global DB_PATH
    DB_PATH = path
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cards (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                yellow INTEGER NOT NULL DEFAULT 0,
                red INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            )
            """
        )
        conn.commit()


def _ensure_row(conn: sqlite3.Connection, chat_id: int, user_id: int, display_name: str) -> None:
    conn.execute(
        """
        INSERT INTO cards (chat_id, user_id, display_name, yellow, red)
        VALUES (?, ?, ?, 0, 0)
        ON CONFLICT(chat_id, user_id) DO UPDATE SET display_name = excluded.display_name
        """,
        (chat_id, user_id, display_name),
    )


def add_yellow_card(chat_id: int, user_id: int, display_name: str, threshold: int) -> tuple[int, int]:
    """Add one yellow card. If the count reaches threshold, reset yellow to 0 and add one red card.

    Returns (yellow_count_after, red_count_after).
    """
    with closing(sqlite3.connect(DB_PATH)) as conn:
        _ensure_row(conn, chat_id, user_id, display_name)
        conn.execute(
            "UPDATE cards SET yellow = yellow + 1 WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )
        yellow, red = conn.execute(
            "SELECT yellow, red FROM cards WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ).fetchone()

        if yellow >= threshold:
            red += 1
            yellow = 0
            conn.execute(
                "UPDATE cards SET yellow = 0, red = ? WHERE chat_id = ? AND user_id = ?",
                (red, chat_id, user_id),
            )

        conn.commit()
        return yellow, red


def list_cards(chat_id: int) -> list[tuple[str, int, int]]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT display_name, yellow, red FROM cards
            WHERE chat_id = ?
            ORDER BY red DESC, yellow DESC, display_name COLLATE NOCASE ASC
            """,
            (chat_id,),
        ).fetchall()
        return rows
