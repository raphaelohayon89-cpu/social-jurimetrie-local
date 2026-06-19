from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
EXPORTS_DIR = ROOT / "exports"
DB_PATH = DATA_DIR / "database.sqlite"
NON_DETECTED = "non détecté"
WARNING_TEXT = (
    "Statistiques indicatives issues du corpus local. Elles ne constituent pas "
    "une analyse exhaustive du contentieux. Les décisions doivent être vérifiées avant citation."
)

VALIDATION_STATUSES = ["à vérifier", "vérifiée", "source vérifiée", "ne pas citer en l'état"]
QUERY_TEMPLATES = [
    "licenciement faute grave refus exécuter instructions",
    "inaptitude obligation reclassement groupe",
    "R.461-9 Code de la sécurité sociale consultation dossier employeur",
    "CRRMP avis non motivé maladie professionnelle",
    "faute inexcusable obligation sécurité conscience du danger",
    "forfait jours suivi charge de travail nullité",
    "salarié protégé autorisation licenciement lien mandat",
    "URSSAF frais professionnels justificatifs redressement",
]

MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}

MODULE_KEYWORDS = {
    "licenciement disciplinaire": ["faute grave", "faute lourde", "licenciement disciplinaire"],
    "inaptitude": ["inaptitude", "reclassement"],
    "harcèlement moral": ["harcèlement moral", "harcelement moral"],
    "discrimination": ["discrimination"],
    "forfait jours": ["forfait jours", "convention de forfait"],
    "heures supplémentaires": ["heures supplémentaires", "heures supplementaires"],
    "salariés protégés": ["salarié protégé", "salarie protege", "statut protecteur"],
    "AT/MP": ["accident du travail", "maladie professionnelle", "faute inexcusable", "crrmp"],
    "URSSAF": ["urssaf", "lettre d'observations", "mise en demeure", "redressement"],
}


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    EXPORTS_DIR.mkdir(exist_ok=True)


def conn() -> sqlite3.Connection:
    ensure_dirs()
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode = MEMORY")
    db.execute("PRAGMA synchronous = NORMAL")
    return db


def init_db() -> None:
    with conn() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                source TEXT NOT NULL,
                source_url TEXT DEFAULT '',
                file_name TEXT DEFAULT '',
                content_hash TEXT NOT NULL UNIQUE,
                imported_at TEXT NOT NULL,
                jurisdiction TEXT DEFAULT 'non détecté',
                decision_date TEXT DEFAULT 'non détecté',
                pourvoi_number TEXT DEFAULT 'non détecté',
                rg_number TEXT DEFAULT 'non détecté',
                matter TEXT DEFAULT 'non détecté',
                sub_matter TEXT DEFAULT 'non détecté',
                keywords TEXT DEFAULT '',
                confidence_score REAL DEFAULT 0,
                validation_status TEXT DEFAULT 'à vérifier',
                citable INTEGER DEFAULT 0,
                notes TEXT DEFAULT ''
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS decisions_fts USING fts5(
                title, raw_text, matter, sub_matter, jurisdiction, content='decisions', content_rowid='id'
            );
            CREATE TRIGGER IF NOT EXISTS decisions_ai AFTER INSERT ON decisions BEGIN
                INSERT INTO decisions_fts(rowid, title, raw_text, matter, sub_matter, jurisdiction)
                VALUES (new.id, new.title, new.raw_text, new.matter, new.sub_matter, new.jurisdiction);
            END;
            """
        )


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(clean(text).encode("utf-8")).hexdigest()


def extract_date(text: str) -> str:
    m = re.search(r"\b([0-3]?\d)[/-]([01]?\d)[/-]((?:19|20)\d{2})\b", text)
    if m:
        day, month, year = map(int, m.groups())
        try:
            return f"{year:04d}-{month:02d}-{day:02d}"
        except ValueError:
            return NON_DETECTED
    m = re.search(r"\b([0-3]?\d)\s+(" + "|".join(MONTHS) + r")\s+((?:19|20)\d{2})\b", text, re.I)
    if m:
        return f"{int(m.group(3)):04d}-{MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    return NON_DETECTED


def extract_jurisdiction(text: str) -> str:
    patterns = [
        r"\bCour de cassation\b",
        r"\bConseil d['’]État\b",
        r"\bCour d['’]appel\s+(?:de|d['’])\s+[A-ZÉÈÀÂÎÏÔÛÙÇ][\w'’ -]+",
        r"\bTribunal judiciaire\s+(?:de|d['’])\s+[A-ZÉÈÀÂÎÏÔÛÙÇ][\w'’ -]+",
        r"\bConseil de prud['’]hommes\s+(?:de|d['’])\s+[A-ZÉÈÀÂÎÏÔÛÙÇ][\w'’ -]+",
        r"\bTribunal administratif\s+(?:de|d['’])\s+[A-ZÉÈÀÂÎÏÔÛÙÇ][\w'’ -]+",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            return clean(m.group(0))
    return NON_DETECTED


def extract_pourvoi(text: str) -> str:
    m = re.search(r"\b(?:pourvoi\s+n[°o]|n[°o]\s+de\s+pourvoi)\s*:?\s*([A-Z]?\s?\d{2}-\d{2}\.\d{3})\b", text, re.I)
    return clean(m.group(1)) if m else NON_DETECTED


def extract_rg(text: str) -> str:
    m = re.search(r"\b(?:RG|R\.G\.|n[°o]\s*RG)\s*n?[°o]?\s*:?\s*([0-9]{2}/[0-9]{3,6})\b", text, re.I)
    return clean(m.group(1)) if m else NON_DETECTED


def classify(text: str) -> tuple[str, str, str, float]:
    lowered = text.lower()
    hits = []
    for sub_matter, keywords in MODULE_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in lowered:
                hits.append((sub_matter, keyword))
    if not hits:
        return NON_DETECTED, NON_DETECTED, "", 0.0
    sub_matter = hits[0][0]
    matter = "droit de la sécurité sociale" if sub_matter in {"AT/MP", "URSSAF"} else "droit du travail"
    keywords = ", ".join(sorted({h[1] for h in hits}))
    return matter, sub_matter, keywords, min(0.95, 0.45 + 0.1 * len(hits))


def title_from_text(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = clean(line)
        if len(line) >= 8:
            return line[:180]
    return fallback


def import_decision(raw_text: str, file_name: str, source: str, source_url: str) -> tuple[int, bool]:
    if not raw_text.strip():
        raise ValueError("Le texte de la décision est vide.")
    matter, sub_matter, keywords, class_conf = classify(raw_text)
    metadata = {
        "title": title_from_text(raw_text, file_name or "Décision importée"),
        "raw_text": raw_text,
        "source": source or "import manuel",
        "source_url": source_url or "",
        "file_name": file_name or "copier-coller.txt",
        "content_hash": content_hash(raw_text),
        "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "jurisdiction": extract_jurisdiction(raw_text),
        "decision_date": extract_date(raw_text),
        "pourvoi_number": extract_pourvoi(raw_text),
        "rg_number": extract_rg(raw_text),
        "matter": matter,
        "sub_matter": sub_matter,
        "keywords": keywords,
        "confidence_score": class_conf,
    }
    with conn() as db:
        existing = db.execute("SELECT id FROM decisions WHERE content_hash = ?", (metadata["content_hash"],)).fetchone()
        if existing:
            return int(existing["id"]), False
        cols = ", ".join(metadata)
        vals = ", ".join("?" for _ in metadata)
        cur = db.execute(f"INSERT INTO decisions ({cols}) VALUES ({vals})", tuple(metadata.values()))
        return int(cur.lastrowid), True


def search_decisions(query: str = "", status: str = "") -> list[dict]:
    where, params = [], []
    if status:
        where.append("validation_status = ?")
        params.append(status)
    with conn() as db:
        if query.strip():
            try:
                q = " AND ".join(f'"{part}"' for part in query.split())
                extra = " AND " + " AND ".join("d." + w for w in where) if where else ""
                rows = db.execute(
                    f"""
                    SELECT d.*, snippet(decisions_fts, 1, '<mark>', '</mark>', '...', 24) AS excerpt
                    FROM decisions_fts JOIN decisions d ON d.id = decisions_fts.rowid
                    WHERE decisions_fts MATCH ? {extra}
                    ORDER BY bm25(decisions_fts) LIMIT 100
                    """,
                    [q, *params],
                ).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.OperationalError:
                pass
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        rows = db.execute(f"SELECT *, substr(raw_text, 1, 300) AS excerpt FROM decisions {where_sql} ORDER BY imported_at DESC", params).fetchall()
        return [dict(r) for r in rows]


def matrix_df() -> pd.DataFrame:
    with conn() as db:
        rows = db.execute(
            """
            SELECT id AS ID, matter AS matière, sub_matter AS sous_matière, jurisdiction AS juridiction,
                   decision_date AS date, pourvoi_number AS numéro_pourvoi, rg_number AS numéro_rg,
                   source, source_url AS lien, confidence_score AS fiabilité_extraction,
                   validation_status AS validation_avocat, notes AS observations,
                   CASE WHEN citable = 1 THEN 'oui' ELSE 'non' END AS décision_citable
            FROM decisions ORDER BY imported_at DESC
            """
        ).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


st.set_page_config(page_title="Social Jurimétrie Local", layout="wide")
ensure_dirs()
init_db()

st.sidebar.title("Social Jurimétrie")
page = st.sidebar.radio(
    "Menu",
    ["Tableau de bord", "Importer", "Recherche", "Fiche décision", "Matrice", "Statistiques", "Paramètres"],
)

if page == "Tableau de bord":
    st.title("Social Jurimétrie Local")
    st.caption("Extraction automatique, validation humaine, citation sécurisée.")
    rows = search_decisions()
    c1, c2, c3 = st.columns(3)
    c1.metric("Décisions", len(rows))
    c2.metric("À vérifier", sum(1 for r in rows if r["validation_status"] == "à vérifier"))
    c3.metric("Citables", sum(1 for r in rows if r["citable"]))
    st.dataframe(matrix_df(), use_container_width=True, hide_index=True)

elif page == "Importer":
    st.title("Importer une décision")
    source = st.text_input("Source", "import manuel")
    source_url = st.text_input("Lien source public, si disponible")
    uploaded = st.file_uploader("Fichier TXT", type=["txt"])
    pasted = st.text_area("Ou coller le texte de la décision", height=260)
    if st.button("Importer", type="primary"):
        try:
            if uploaded:
                raw = uploaded.getvalue().decode("utf-8", errors="replace")
                name = uploaded.name
            else:
                raw, name = pasted, "copier-coller.txt"
            decision_id, created = import_decision(raw, name, source, source_url)
            st.session_state["decision_id"] = decision_id
            st.success(f"Décision {'importée' if created else 'déjà présente'} : ID {decision_id}")
        except Exception as exc:
            st.error(f"Import impossible : {exc}")

elif page == "Recherche":
    st.title("Recherche")
    template = st.selectbox("Requête type", ["", *QUERY_TEMPLATES])
    query = st.text_input("Recherche plein texte", template)
    status = st.selectbox("Validation", ["", *VALIDATION_STATUSES])
    results = search_decisions(query, status)
    for row in results:
        with st.container(border=True):
            st.markdown(f"**ID {row['id']} - {row['title']}**")
            st.caption(f"{row['jurisdiction']} | {row['decision_date']} | {row['matter']} | {row['validation_status']}")
            st.markdown(row.get("excerpt") or "", unsafe_allow_html=True)
            if st.button("Ouvrir", key=f"open-{row['id']}"):
                st.session_state["decision_id"] = row["id"]

elif page == "Fiche décision":
    st.title("Fiche décision")
    decision_id = st.number_input("ID décision", min_value=1, value=int(st.session_state.get("decision_id", 1)))
    with conn() as db:
        row = db.execute("SELECT * FROM decisions WHERE id = ?", (int(decision_id),)).fetchone()
    if not row:
        st.info("Aucune décision pour cet ID.")
    else:
        d = dict(row)
        st.subheader(d["title"])
        st.write({k: d[k] for k in ["jurisdiction", "decision_date", "pourvoi_number", "rg_number", "matter", "sub_matter", "confidence_score"]})
        status = st.selectbox("Statut", VALIDATION_STATUSES, index=VALIDATION_STATUSES.index(d["validation_status"]) if d["validation_status"] in VALIDATION_STATUSES else 0)
        citable = st.checkbox("Décision citable en l'état", value=bool(d["citable"]))
        notes = st.text_area("Notes avocat", d["notes"] or "")
        if st.button("Enregistrer", type="primary"):
            with conn() as db:
                db.execute("UPDATE decisions SET validation_status=?, citable=?, notes=? WHERE id=?", (status, int(citable), notes, int(decision_id)))
            st.success("Validation enregistrée.")
        st.text_area("Texte intégral", d["raw_text"], height=420, disabled=True)

elif page == "Matrice":
    st.title("Matrice jurisprudentielle")
    df = matrix_df()
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.download_button(
        "Télécharger CSV",
        df.to_csv(index=False).encode("utf-8-sig"),
        "matrice-jurisprudentielle.csv",
        "text/csv",
    )

elif page == "Statistiques":
    st.title("Statistiques indicatives")
    st.warning(WARNING_TEXT)
    df = matrix_df()
    if df.empty:
        st.info("Aucune décision dans le corpus.")
    else:
        st.subheader("Par matière")
        st.dataframe(df.groupby("matière").size().reset_index(name="nombre"), hide_index=True, use_container_width=True)
        st.subheader("Par juridiction")
        st.dataframe(df.groupby("juridiction").size().reset_index(name="nombre"), hide_index=True, use_container_width=True)

else:
    st.title("Paramètres")
    st.write("Aucun secret n'est nécessaire pour cette V1. Les clés API doivent rester hors du code.")
