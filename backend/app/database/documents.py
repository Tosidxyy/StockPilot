"""Optional FTS5 external index; ordinary chunk storage remains usable without it."""
from sqlalchemy.exc import OperationalError


def init_document_fts(engine):
    try:
        with engine.begin() as connection:
            existing = connection.exec_driver_sql("SELECT count(*) FROM sqlite_master WHERE name='document_fts'").scalar()
            connection.exec_driver_sql("""CREATE VIRTUAL TABLE IF NOT EXISTS document_fts
                USING fts5(title, text, content='document_chunk', content_rowid='id', tokenize='trigram')""")
            connection.exec_driver_sql("""CREATE TRIGGER IF NOT EXISTS document_chunk_insert AFTER INSERT ON document_chunk BEGIN
                INSERT INTO document_fts(rowid,title,text) VALUES (new.id,new.title,new.text); END""")
            connection.exec_driver_sql("""CREATE TRIGGER IF NOT EXISTS document_chunk_delete AFTER DELETE ON document_chunk BEGIN
                INSERT INTO document_fts(document_fts,rowid,title,text) VALUES ('delete',old.id,old.title,old.text); END""")
            connection.exec_driver_sql("""CREATE TRIGGER IF NOT EXISTS document_chunk_update AFTER UPDATE ON document_chunk BEGIN
                INSERT INTO document_fts(document_fts,rowid,title,text) VALUES ('delete',old.id,old.title,old.text);
                INSERT INTO document_fts(rowid,title,text) VALUES (new.id,new.title,new.text); END""")
            if not existing:
                connection.exec_driver_sql("INSERT INTO document_fts(document_fts) VALUES ('rebuild')")
    except OperationalError as error:
        # SQLite builds without FTS5/trigram use bounded parameterized LIKE queries.
        if not any(message in str(error).lower() for message in ("no such module: fts5", "no such tokenizer", "error in tokenizer constructor")):
            raise
        return False
    return True
