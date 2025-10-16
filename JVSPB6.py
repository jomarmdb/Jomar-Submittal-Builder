import streamlit as st
import pandas as pd
import requests
from io import BytesIO
from PyPDF2 import PdfMerger
from datetime import datetime
import tempfile, os

# =========================
# Cover Page (Jomar style)
# =========================
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader

def hex_to_rgb01(hex_color: str):
    h = hex_color.strip().lstrip("#")
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    return r, g, b

def try_register_font(ttf_path: str, face_name: str):
    if ttf_path and os.path.exists(ttf_path):
        try:
            pdfmetrics.registerFont(TTFont(face_name, ttf_path))
            return face_name
        except Exception:
            pass
    return "Helvetica"

def draw_logo_centered_between_page_top_and_bar_top(c, logo_path, max_width, page_width, page_height, bar_top_y):
    img = ImageReader(logo_path)
    iw, ih = img.getSize()
    scale = min(max_width / iw, 1.0)
    w = iw * scale
    h = ih * scale
    x = (page_width - w) / 2.0
    desired_center_y = (page_height + bar_top_y) / 2.0
    y = desired_center_y - (h / 2.0)
    y = min(y, page_height - h - 24)
    c.drawImage(logo_path, x, y, width=w, height=h, preserveAspectRatio=True, mask='auto')

def make_cover_pdf(
    outfile: str,
    logo_path: str,
    project_name: str,
    project_location: str,
    contractor: str,
    date_prepared,
    bid_date,
    font_path_light: str = ""
):
    c = canvas.Canvas(outfile, pagesize=letter)
    width, height = letter

    font_light = try_register_font(font_path_light, "ProximaNova-Light")

    # Red bar (lowered & taller), locked to #BC141B
    BAR_COLOR = "#BC141B"
    bar_rgb   = hex_to_rgb01(BAR_COLOR)
    bar_height = 150
    bar_y      = (height / 2) - 10
    bar_top_y  = bar_y + bar_height

    c.setFillColorRGB(*bar_rgb)
    c.rect(0, bar_y, width, bar_height, stroke=0, fill=1)

    # Logo centered between page top and bar top
    if logo_path and os.path.exists(logo_path):
        try:
            draw_logo_centered_between_page_top_and_bar_top(
                c, logo_path, max_width=220, page_width=width, page_height=height, bar_top_y=bar_top_y
            )
        except Exception as e:
            # Render app warning via Streamlit only when running inside Streamlit
            try: st.warning(f"Logo draw error: {e}")
            except: pass

    # White stacked text inside bar (ALL CAPS, not bold)
    c.setFillColorRGB(1, 1, 1)
    c.setFont(font_light, 24)
    c.drawCentredString(width/2, bar_y + bar_height - 40, (project_name or "PROJECT NAME").upper())
    c.setFont(font_light, 16)
    c.drawCentredString(width/2, bar_y + bar_height - 72, (project_location or "PROJECT LOCATION").upper())
    c.setFont(font_light, 13)
    c.drawCentredString(width/2, bar_y + 22, "SUBMITTAL PACKAGE")

    # Bottom fields (left aligned)
    left_margin = 50
    base_y = 120
    line_gap = 18
    c.setFillColorRGB(0, 0, 0)
    c.setFont(font_light, 11)
    c.drawString(left_margin, base_y + line_gap * 2, f"Contractor: {(contractor or '').strip()}")
    dp = date_prepared.strftime("%B %d, %Y") if date_prepared else ""
    bd = bid_date.strftime("%B %d, %Y") if bid_date else ""
    c.drawString(left_margin, base_y + line_gap, f"Date Prepared: {dp}")
    c.drawString(left_margin, base_y, f"Bid Date: {bd}")

    c.showPage()
    c.save()

# =========================
# App UI / Logic
# =========================
st.title("Valve Spec Sheet Combiner — Catalog View")
st.caption("Select by Category → Subcategory → Product. Add uploads, manage a queue, and generate a combined PDF with a Jomar-styled cover.")

# ---- Configuration (edit these paths as needed) ----
EXCEL_PATH = st.text_input("Excel library path (.xlsx)", "spec_sheet_library.xlsx")
DEFAULT_LOGO_PATH = r"C:\Users\Matt.Bianchi\OneDrive - jomar.com\Jomar\Company Info\Logos\Jomar Valve Logo Red.png"
PROXIMA_TTF = st.text_input("Optional: Proxima Nova Light .ttf path (leave blank for Helvetica)", "")

# ---- Load library ----
@st.cache_data(show_spinner=False)
def load_library(xlsx_path):
    df = pd.read_excel(xlsx_path)
    # Normalize expected column names
    expected = {"Category","Subcategory","Model","Description","URL","Image"}
    missing = [c for c in expected if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in Excel: {missing}")
    # Drop rows without URL or Model
    df = df.dropna(subset=["Model","URL"]).copy()
    return df

try:
    library = load_library(EXCEL_PATH)
except Exception as e:
    st.error(f"Unable to load Excel file: {e}")
    st.stop()

# ---- Session state (queue + uploads) ----
if "queue" not in st.session_state:
    # queue items are dicts: {Model, URL, Category, Subcategory, Description, Image}
    st.session_state.queue = []
if "uploads" not in st.session_state:
    st.session_state.uploads = []

# ---- Filters: Category → Subcategory ----
cols = st.columns(2)
with cols[0]:
    category = st.selectbox("Category", sorted(library["Category"].dropna().unique()))
with cols[1]:
    sub_df = library[library["Category"] == category]
    subcategory = st.selectbox("Subcategory", sorted(sub_df["Subcategory"].dropna().unique()))

filtered = library[(library["Category"] == category) & (library["Subcategory"] == subcategory)]

st.markdown("### Products")
if filtered.empty:
    st.info("No products found for this selection.")
else:
    # Image grid: each product shows image, linked model, description, and Add button
    for _, row in filtered.iterrows():
        c1, c2 = st.columns([1, 3], vertical_alignment="center")
        with c1:
            try:
                st.image(row["Image"], width=110)
            except Exception:
                st.write("No image")
        with c2:
            model = str(row["Model"])
            url = str(row["URL"])
            desc = str(row.get("Description", "") or "")
            st.markdown(f"[**{model}**]({url})  \n{desc}")
            if st.button(f"Add {model}", key=f"add_{category}_{subcategory}_{model}"):
                # prevent duplicates by Model+URL
                if not any((qi["Model"] == model and qi["URL"] == url) for qi in st.session_state.queue):
                    st.session_state.queue.append({
                        "Category": row["Category"],
                        "Subcategory": row["Subcategory"],
                        "Model": model,
                        "Description": desc,
                        "URL": url,
                        "Image": row["Image"]
                    })
                st.success(f"✓ Added {model}")

# ---- Optional user uploads (drag & drop) ----
st.markdown("---")
st.subheader("Optional: Drag & drop additional PDFs")
uploaded_files = st.file_uploader(
    "Add extra PDFs that aren’t in the library (they’ll be merged after the cover).",
    type="pdf",
    accept_multiple_files=True
)
# Store uploads in session state so they persist while navigating
if uploaded_files:
    # Add newly uploaded files by id/name (avoid duplicates by name + size)
    new_count = 0
    existing_keys = {(f.name, f.size) for f in st.session_state.uploads}
    for f in uploaded_files:
        key = (f.name, f.size)
        if key not in existing_keys:
            st.session_state.uploads.append(f)
            existing_keys.add(key)
            new_count += 1
    if new_count:
        st.success(f"✓ Added {new_count} uploaded file(s).")

# ---- Queue / Cart panel ----
st.markdown("---")
st.subheader("Queue")
if not st.session_state.queue and not st.session_state.uploads:
    st.write("No items in the queue yet.")
else:
    if st.session_state.queue:
        st.write("**From Library:**")
        qdf = pd.DataFrame(st.session_state.queue)[["Category","Subcategory","Model","URL"]]
        st.dataframe(qdf, use_container_width=True)
    if st.session_state.uploads:
        st.write("**Uploaded PDFs:**")
        st.write("• " + "  \n• ".join([f.name for f in st.session_state.uploads]))

    cA, cB = st.columns(2)
    with cA:
        if st.button("🧹 Clear Queue"):
            st.session_state.queue = []
            st.session_state.uploads = []
            st.success("Queue cleared.")

# ---- Cover fields ----
st.markdown("---")
st.subheader("Cover Page")
project_name = st.text_input("Project Name", "")
project_location = st.text_input("Project Location", "")
contractor_name = st.text_input("Contractor", "")
date_prepared = st.date_input("Date Prepared")
bid_date = st.date_input("Bid Date")
logo_path = st.text_input("Logo path", DEFAULT_LOGO_PATH)

# ---- Generate Combined PDF ----
if st.session_state.queue or st.session_state.uploads:
    if st.button("Generate Combined PDF"):
        # Build cover
        cover_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        make_cover_pdf(
            cover_tmp.name,
            logo_path=logo_path,
            project_name=project_name,
            project_location=project_location,
            contractor=contractor_name,
            date_prepared=date_prepared,
            bid_date=bid_date,
            font_path_light=PROXIMA_TTF,
        )

        # Merge cover + selected PDFs
        merger = PdfMerger()
        merger.append(cover_tmp.name)

        # 1) Library items (download by URL in the order added)
        for item in st.session_state.queue:
            url = item["URL"]
            try:
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                merger.append(BytesIO(resp.content))
            except Exception as e:
                st.warning(f"Could not add {item['Model']} from {url}: {e}")

        # 2) Uploaded local PDFs (in the order added)
        for up in st.session_state.uploads:
            try:
                merger.append(up)
            except Exception as e:
                st.warning(f"Could not add uploaded file {up.name}: {e}")

        # Write output
        output = BytesIO()
        merger.write(output)
        merger.close()
        output.seek(0)

        st.download_button(
            "⬇️ Download Combined PDF",
            data=output,
            file_name="Combined_Spec_Sheets.pdf",
            mime="application/pdf"
        )
