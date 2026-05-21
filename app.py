"""
CSF → MCC Verification PoC
----------------------------
Upload a CSF PDF  →  extract text  →  classify to MCC codes  →  merchant confirms.

Classification:
  - If ANTHROPIC_API_KEY is set: calls Claude for high-accuracy classification.
  - Otherwise: uses the built-in keyword classifier (no API key required).

Run locally:
    python3 app.py

Run for production (gunicorn):
    gunicorn app:app
"""

import os
import re
import json
import textwrap
import uuid
from pathlib import Path

import httpx
import pdfplumber
from flask import Flask, request, render_template

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret-change-in-prod")

UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-3-5-haiku-20241022"

# ISO 18245 / Visa-Mastercard subset — extend for your merchant mix
MCC_TABLE: dict[str, str] = {
    "4111": "Local and Suburban Commuter Transportation",
    "4121": "Taxicabs and Limousines",
    "4722": "Travel Agencies and Tour Operators",
    "5045": "Computers, Peripherals and Software",
    "5047": "Medical and Dental Laboratories",
    "5065": "Electrical Parts and Equipment",
    "5310": "Discount Stores",
    "5331": "Variety Stores",
    "5411": "Grocery Stores, Supermarkets",
    "5462": "Bakeries",
    "5651": "Family Clothing Stores",
    "5661": "Shoe Stores",
    "5691": "Men's and Women's Clothing Stores",
    "5699": "Miscellaneous Apparel and Accessory Shops",
    "5732": "Electronics Stores",
    "5734": "Computer Software Stores",
    "5812": "Eating Places, Restaurants",
    "5814": "Fast Food Restaurants",
    "5941": "Sporting Goods Stores",
    "5944": "Jewelry Stores",
    "5945": "Hobby, Toy and Game Shops",
    "5977": "Cosmetic Stores",
    "5999": "Miscellaneous Food Stores",
    "7011": "Hotels and Motels",
    "7230": "Barber and Beauty Shops",
    "7298": "Health and Beauty Spas",
    "7372": "Computer Programming and Data Processing",
    "7374": "Computer Processing and Data Preparation",
    "7999": "Recreation Services — Not Elsewhere Classified",
    "8011": "Doctors and Physicians",
    "8021": "Dentists and Orthodontists",
    "8099": "Health Practitioners — Not Elsewhere Classified",
    "8299": "Schools and Educational Services",
    "8398": "Charitable and Social Service Organizations",
    "8999": "Professional Services — Not Elsewhere Classified",
}


# ---------------------------------------------------------------------------
# Keyword classifier (no API key required)
# ---------------------------------------------------------------------------

# Each entry: (mcc_code, [keywords], summary_template, rationale_template)
_KEYWORD_RULES: list[tuple] = [
    (
        "5462",
        ["baker", "panaderi", "pasteleri", "bread", "pan ", "bollilo", "reposteria",
         "pastry", "boulangerie", "panificacion", "bäckerei"],
        "A bakery producing and selling bread, pastries, and baked goods.",
        "Document lists baking or bread-making as a primary registered activity.",
    ),
    (
        "5812",
        ["restauran", "comida", "dining", "eatery", "cuisine", "gastro", "bistro",
         "meals", "food service", "alimentos preparados", "traiteur", "caterin"],
        "A restaurant or food-service business serving prepared meals.",
        "Document describes prepared-food service as the primary commercial activity.",
    ),
    (
        "5814",
        ["fast food", "comida rapida", "burger", "pizza", "kebab", "takeaway",
         "take-away", "drive-through", "quick service"],
        "A fast-food or quick-service restaurant.",
        "Document describes a quick-service or fast-food operation.",
    ),
    (
        "5411",
        ["supermarket", "supermercado", "grocery", "abarrotes", "verduleria",
         "fresh produce", "frutas y verduras", "épicerie", "alimentacion general"],
        "A grocery store or supermarket selling food and household staples.",
        "Document lists general food retail as the primary economic activity.",
    ),
    (
        "5691",
        ["clothing", "ropa", "apparel", "garment", "fashion", "textil", "boutique",
         "vêtement", "kleidung", "confeccion"],
        "A clothing and apparel retail store.",
        "Document identifies clothing or apparel retail as the main activity.",
    ),
    (
        "5977",
        ["cosmetic", "cosmetico", "beauty product", "makeup", "skincare",
         "perfume", "fragrance", "beauté", "cosmetik"],
        "A cosmetics and beauty products retailer.",
        "Document lists cosmetics or beauty products as the primary product line.",
    ),
    (
        "7230",
        ["barber", "salon", "peluqueria", "hairdress", "coiffure", "hair cut",
         "barbershop", "hairsalon", "friseur"],
        "A barbershop or hair salon providing personal grooming services.",
        "Document describes hair or grooming services as the primary activity.",
    ),
    (
        "7298",
        ["spa", "wellness", "massage", "terapia", "relaxation", "beauty spa",
         "estetica", "esthétique", "schönheit"],
        "A health and beauty spa offering wellness treatments.",
        "Document lists spa, wellness, or beauty treatments as the main service.",
    ),
    (
        "5912",
        ["pharmacy", "farmacia", "drug store", "medicament", "apotek",
         "pharmacie", "apotheke", "drogueria"],
        "A pharmacy or drug store.",
        "Document identifies pharmaceutical retail as the primary registered activity.",
    ),
    (
        "8011",
        ["doctor", "physician", "medicina general", "consulta medica", "medico",
         "médecin", "arzt", "clinica", "clinic"],
        "A medical practice providing doctor or physician services.",
        "Document describes medical consulting or physician services.",
    ),
    (
        "8021",
        ["dentist", "dental", "odontolog", "orthodont", "dentaire", "zahnarzt"],
        "A dental practice.",
        "Document lists dental or orthodontic services as the primary activity.",
    ),
    (
        "7372",
        ["software", "desarrollo", "programming", "it service", "tech",
         "informatica", "digital", "saas", "desarrollo web", "app development"],
        "A technology or software development company.",
        "Document describes software development or IT services as the primary activity.",
    ),
    (
        "7011",
        ["hotel", "motel", "hostel", "alojamiento", "accommodation",
         "lodging", "inn", "hébergement", "unterkunft"],
        "A hotel or motel providing overnight accommodation.",
        "Document identifies lodging or accommodation as the primary service.",
    ),
    (
        "4722",
        ["travel", "turismo", "tour operator", "agencia de viajes", "viajes",
         "tourisme", "reisebüro", "excursion"],
        "A travel agency or tour operator.",
        "Document lists travel services or tour operations as the main activity.",
    ),
    (
        "5732",
        ["electronics", "electronico", "electronic store", "gadget", "appliance",
         "electrónica", "électronique", "elektronik"],
        "An electronics retail store.",
        "Document describes consumer electronics as the primary product category.",
    ),
    (
        "8299",
        ["school", "escuela", "education", "academy", "learning", "training",
         "instituto", "colegio", "université", "capacitacion"],
        "An educational institution or training provider.",
        "Document identifies educational services as the primary activity.",
    ),
    (
        "5941",
        ["sport", "deporte", "gym", "fitness", "atletismo", "sporting goods",
         "articulos deportivos", "fitnessstudio"],
        "A sporting goods store or fitness-related business.",
        "Document lists sports equipment or fitness services.",
    ),
]


def _keyword_classify(text: str) -> dict:
    """
    Score the extracted text against keyword rules and return a classification
    result in the same shape as the Claude classifier.
    """
    lower = text.lower()
    scores: list[tuple[int, str, str, str]] = []  # (hits, code, summary, rationale)

    for code, keywords, summary, rationale in _KEYWORD_RULES:
        hits = sum(1 for kw in keywords if kw in lower)
        if hits:
            scores.append((hits, code, summary, rationale))

    scores.sort(key=lambda x: -x[0])

    if not scores:
        # Fallback — not enough signal
        return {
            "business_summary": "Unable to identify a specific business activity from the document.",
            "candidates": [
                {
                    "mcc_code": "8999",
                    "mcc_description": MCC_TABLE["8999"],
                    "confidence": "low",
                    "rationale": "No strong keyword signals found. Manual review recommended.",
                }
            ],
            "recommended_mcc": "8999",
            "recommended_description": MCC_TABLE["8999"],
            "ambiguities": ["Document does not contain recognisable activity keywords."],
        }

    candidates = []
    for rank, (hits, code, summary, rationale) in enumerate(scores[:3]):
        confidence = "high" if rank == 0 and hits >= 2 else ("medium" if hits >= 1 else "low")
        candidates.append({
            "mcc_code": code,
            "mcc_description": MCC_TABLE.get(code, ""),
            "confidence": confidence,
            "rationale": rationale,
        })

    top = scores[0]
    ambiguities = []
    if len(scores) > 1 and scores[1][0] >= scores[0][0] - 1:
        ambiguities.append(
            f"Activity signals for {MCC_TABLE.get(scores[1][1], scores[1][1])} "
            f"({scores[1][1]}) are nearly as strong — consider reviewing."
        )

    return {
        "business_summary": top[2],
        "candidates": candidates,
        "recommended_mcc": top[1],
        "recommended_description": MCC_TABLE.get(top[1], ""),
        "ambiguities": ambiguities,
    }


# ---------------------------------------------------------------------------
# Claude classifier (used when ANTHROPIC_API_KEY is set)
# ---------------------------------------------------------------------------

def _claude_classify(text: str, country: str) -> dict:
    mcc_list = "\n".join(f"  {code}: {desc}" for code, desc in MCC_TABLE.items())

    prompt = textwrap.dedent(f"""
        You are an MCC (Merchant Category Code) classification analyst for a payments platform.

        Analyse the text extracted from a sub-merchant's fiscal registration document (CSF).
        Map their primary economic activity to the most appropriate MCC code(s) from the list below.

        Rules:
        - Use ONLY codes from the reference list. Do NOT invent codes.
        - Return up to 3 candidates, ranked by confidence (best first).
        - Confidence: "high" = clear single match; "medium" = plausible; "low" = weak signal.
        - Write business_summary in plain English a non-expert merchant would understand.
        - Flag ambiguities (multiple activities, vague descriptions, regulated sectors).
        - Country of operation: {country}

        MCC Reference List:
        {mcc_list}

        Extracted Document Text:
        <untrusted_document>
        {text}
        </untrusted_document>

        Respond ONLY with valid JSON — no prose, no markdown fences:
        {{
          "business_summary": "...",
          "candidates": [
            {{"mcc_code": "5462", "mcc_description": "Bakeries", "confidence": "high", "rationale": "..."}}
          ],
          "recommended_mcc": "5462",
          "recommended_description": "Bakeries",
          "ambiguities": ["..."]
        }}
    """).strip()

    resp = httpx.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={"model": MODEL, "max_tokens": 1024,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=30,
    )
    resp.raise_for_status()

    raw = resp.json()["content"][0]["text"].strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()

    return json.loads(raw)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def classify_mcc(text: str, country: str = "unknown") -> dict:
    if ANTHROPIC_KEY:
        return _claude_classify(text, country)
    return _keyword_classify(text)


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def extract_text_from_pdf(pdf_path: Path) -> str:
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(f"[Page {i}]\n{text.strip()}")
    if not pages:
        raise ValueError(
            "No extractable text found. The PDF may be a scanned image — "
            "please upload a text-layer PDF."
        )
    return "\n\n".join(pages)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return render_template("index.html", demo_mode=not bool(ANTHROPIC_KEY))


@app.post("/upload")
def upload():
    if "csf_file" not in request.files:
        return render_template("index.html", error="No file selected.", demo_mode=not bool(ANTHROPIC_KEY))

    f = request.files["csf_file"]
    if not f.filename:
        return render_template("index.html", error="No file selected.", demo_mode=not bool(ANTHROPIC_KEY))
    if not f.filename.lower().endswith(".pdf"):
        return render_template("index.html", error="Only PDF files are supported.", demo_mode=not bool(ANTHROPIC_KEY))

    uid = uuid.uuid4().hex[:8]
    save_path = UPLOAD_DIR / f"{uid}_csf.pdf"
    f.save(save_path)

    try:
        text = extract_text_from_pdf(save_path)
    except ValueError as e:
        save_path.unlink(missing_ok=True)
        return render_template("index.html", error=str(e), demo_mode=not bool(ANTHROPIC_KEY))
    finally:
        save_path.unlink(missing_ok=True)

    country = request.form.get("country", "unknown").strip() or "unknown"

    try:
        result = classify_mcc(text, country=country)
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        return render_template("index.html", error=f"Classification failed: {e}", demo_mode=not bool(ANTHROPIC_KEY))

    mcc_options = [
        {"code": code, "description": desc}
        for code, desc in sorted(MCC_TABLE.items())
    ]

    return render_template(
        "result.html",
        business_summary=result.get("business_summary", ""),
        candidates=result.get("candidates", []),
        recommended_mcc=result.get("recommended_mcc", ""),
        recommended_description=result.get("recommended_description", ""),
        ambiguities=result.get("ambiguities", []),
        mcc_options=mcc_options,
        filename=f.filename,
        demo_mode=not bool(ANTHROPIC_KEY),
    )


@app.post("/confirm")
def confirm():
    confirmed_mcc = request.form.get("confirmed_mcc", "").strip()
    confirmed_description = MCC_TABLE.get(
        confirmed_mcc, request.form.get("confirmed_description", "")
    )
    return render_template(
        "confirmed.html",
        confirmed_mcc=confirmed_mcc,
        confirmed_description=confirmed_description,
        verification_path=request.form.get("verification_path", "csf-confirmed"),
        business_summary=request.form.get("business_summary", ""),
        demo_mode=not bool(ANTHROPIC_KEY),
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)
