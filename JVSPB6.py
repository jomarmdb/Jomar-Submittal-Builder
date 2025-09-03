import streamlit as st
from PyPDF2 import PdfMerger
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.utils import ImageReader
from datetime import datetime
import tempfile, os

# ---------- Drag & drop ordering (with fallback) ----------
def _get_sort_labels_fn():
    try:
        from streamlit_sortables import sort_items
        def sort_labels(labels, key="file_order"):
            st.markdown("**Drag to set the order of spec sheets (cover page will remain first):**")
            return sort_items(labels, direction="vertical", multi_containers=False, key=key)
        return sort_labels
    except Exception:
        pass

    # Fallback: simple numeric ordering UI
    def sort_labels(labels, key="file_order"):
        st.info("Drag component not available. Enter desired order numbers (1..N).")
        orders = []
        for i, name in enumerate(labels):
            cols = st.columns([1, 6])
            with cols[0]:
                o = st.number_input(
                    "Order", min_value=1, max_value=len(labels),
                    value=i+1, key=f"ord_{key}_{i}", label_visibility="collapsed"
                )
            with cols[1]:
                st.write(name)
            orders.append((o, name))
        orders.sort(key=lambda t: t[0])
        return [name for _, name in orders]
    return sort_labels

sort_labels = _get_sort_labels_fn()

# ---------- Helpers ----------
def hex_to_rgb01(hex_color: str):
    h = hex_color.strip().lstrip("#")
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    return r, g, b

def fit_multiline_text(lines, font_name, bar_width, bar_height,
                       side_pad=48, v_pad=18,
                       max_pt=36, min_pt=14,
                       leading_factor=1.12, letter_spacing=0.0):
    """
    Compute a single font size (applied to all lines) and matching leading
    that fit within the red bar's width/height after padding.
    """
    # Safe drawing box inside the bar
    safe_w = max(bar_width - 2*side_pad, 1)
    safe_h = max(bar_height - 2*v_pad, 1)

    # Width cap: the size at which the *widest* line would just fit
    caps = []
    for txt in lines:
        if not txt:
            continue
        base_w_at_1pt = pdfmetrics.stringWidth(txt, font_name, 1.0)
        # letter_spacing is absolute points (not scaled by size)
        extra = letter_spacing * max(len(txt) - 1, 0)
        unit_w = base_w_at_1pt  # we'll keep letter_spacing=0 for titles by default
        if unit_w > 0:
            caps.append(safe_w / unit_w)
    width_cap = min(caps) if caps else max_pt

    # Height cap: total stack height must fit (N-1) * leading
    n = len(lines)
    height_cap = (safe_h / ((n - 1) * leading_factor)) if n > 1 else max_pt

    size = max(min(width_cap, height_cap, max_pt), min_pt)
    leading = size * leading_factor
    return [size] * n, leading

def draw_logo_centered_between_page_top_and_bar_top(c, logo_path, max_width, page_width, page_height, bar_top_y):
    """Draw logo centered horizontally, vertically centered between page top and top of the red bar."""
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
    return h

def draw_centered_stack(
    c, x_center, y_center, lines, sizes, font_name, color_rgb, leading=26, letter_spacing=0.0, optical_adjust=0.0
):
    """
    Vertically center a multi-line block using the font's ascent/descent so the
    visible text is centered (not just baselines). 'optical_adjust' lets you nudge
    the block a point or two if desired.
    """
    if not lines:
        return

    # Font metrics (ReportLab returns 1000-em units; scale by point size)
    asc_u = pdfmetrics.getAscent(font_name) / 1000.0
    des_u = abs(pdfmetrics.getDescent(font_name) / 1000.0)

    asc0      = asc_u * sizes[0]          # ascent of first line
    des_last  = des_u * sizes[-1]         # descent of last line
    interline = leading * (len(lines) - 1)

    # Total visible block height = top of first line to bottom of last line
    block_h = asc0 + interline + des_last

    # Baseline of the first line so that the block's vertical center == y_center
    first_baseline_y = y_center + (block_h / 2.0) - asc0 + optical_adjust

    c.setFillColorRGB(*color_rgb)

    for i, (txt, sz) in enumerate(zip(lines, sizes)):
        y = first_baseline_y - i * leading
        c.setFont(font_name, sz)
        if letter_spacing and letter_spacing > 0:
            # Center with tracking: widen width by char spacing * number of gaps
            n_gaps = max(len(txt) - 1, 0)
            base_w = pdfmetrics.stringWidth(txt, font_name, sz)
            w = base_w + letter_spacing * n_gaps
            x_left = x_center - (w / 2.0)

            t = c.beginText()
            t.setTextOrigin(x_left, y)
            t.setFont(font_name, sz)
            try:
                t.setCharSpace(letter_spacing)
            except Exception:
                pass
            t.textLine(txt)
            c.drawText(t)
        else:
            c.drawCentredString(x_center, y, txt)

def format_mdY(d, blank="To Be Confirmed"):
    """Return M/D/YYYY without leading zeros; if None, return given blank text."""
    if not d:
        return blank
    return f"{d.month}/{d.day}/{d.year}"

# --- helper: date picker that can be "unknown" (returns None when unknown) ---
def date_or_tbc(label: str, key: str, unknown_label: str | None = None):
    unknown_key = f"{key}_unknown"
    unknown_state = st.session_state.get(unknown_key, False)
    date_val = st.date_input(label, key=f"{key}_date", disabled=unknown_state)
    st.checkbox(
        unknown_label or f"{label} unknown",
        key=unknown_key,
        value=unknown_state,
        help=f"If checked, the {label} will show 'To Be Confirmed' on the cover."
    )
    return None if st.session_state.get(unknown_key, False) else date_val

# --- helper: mutually-exclusive checkboxes for role selection ---
def role_checkbox_group(key_prefix="role"):
    roles = ["Contractor", "Engineer", "Distributor", "Utility"]
    keys = [f"{key_prefix}_{r.lower()}" for r in roles]

    def _set_only(this_key):
        for k in keys:
            if k != this_key:
                st.session_state[k] = False

    cols = st.columns(len(roles))
    for r, k, col in zip(roles, keys, cols):
        with col:
            st.checkbox(r, key=k, on_change=_set_only, args=(k,))

    for r, k in zip(roles, keys):
        if st.session_state.get(k):
            return r
    return None  # nothing selected yet

# --- helper: Bid Date with two *mutually exclusive* flags (TBC / Not Applicable) ---
def bid_date_picker_with_flags(label: str, key: str):
    """
    Date input + two checkboxes:
      - 'Bid Date To Be Confirmed' -> show 'BID DATE: TO BE CONFIRMED'
      - 'Bid Date Not Applicable'  -> omit the BID DATE line

    Only one (or none) can be checked at a time.
    """
    tbc_key, na_key = f"{key}_tbc", f"{key}_na"

    # Make them mutually exclusive via callbacks
    def _on_tbc_change():
        if st.session_state.get(tbc_key, False):
            st.session_state[na_key] = False

    def _on_na_change():
        if st.session_state.get(na_key, False):
            st.session_state[tbc_key] = False

    # date input disabled if either flag is set
    disabled = st.session_state.get(tbc_key, False) or st.session_state.get(na_key, False)
    date_val = st.date_input(label, key=f"{key}_date", disabled=disabled)

    cols = st.columns(2)
    with cols[0]:
        st.checkbox("Bid Date To Be Confirmed", key=tbc_key, on_change=_on_tbc_change)
    with cols[1]:
        st.checkbox("Bid Date Not Applicable",  key=na_key,  on_change=_on_na_change)

    # derive final states after potential callback flips
    tbc_state = st.session_state.get(tbc_key, False)
    na_state  = st.session_state.get(na_key,  False)

    if tbc_state or na_state:
        date_val = None

    return date_val, tbc_state, na_state

def make_cover_pdf(
    outfile: str,
    logo_path: str,
    project_name: str,
    project_location: str,
    party_label: str,
    party_name: str,
    date_prepared,
    bid_date,
    bid_date_tbc: bool = False,
    bid_date_na: bool = False,
):
    c = canvas.Canvas(outfile, pagesize=letter)
    width, height = letter

    # Fonts (built-in)
    FONT_TITLE = "Helvetica"
    FONT_TEXT  = "Helvetica"

    # ---- Light gray inner border ----
    border_inset = 36
    c.setLineWidth(1)
    c.setStrokeColorRGB(*hex_to_rgb01("#D9D9D9"))
    c.rect(border_inset, border_inset, width - 2*border_inset, height - 2*border_inset, stroke=1, fill=0)

    # ---- Red bar ----
    BAR_COLOR  = "#BC141B"
    bar_rgb    = hex_to_rgb01(BAR_COLOR)
    bar_height = 140
    bar_y      = (height / 2.0) - (bar_height / 2.0)
    bar_top_y  = bar_y + bar_height

    c.setFillColorRGB(*bar_rgb)
    c.setStrokeColorRGB(*bar_rgb)
    c.rect(0, bar_y, width, bar_height, stroke=0, fill=1)

    # ---- Logo: centered between page top and bar top ----
    if logo_path and os.path.exists(logo_path):
        try:
            draw_logo_centered_between_page_top_and_bar_top(
                c, logo_path, max_width=300,
                page_width=width, page_height=height, bar_top_y=bar_top_y
            )
        except Exception as e:
            st.warning(f"Logo draw error: {e}")
    else:
        st.warning(f"Logo file not found at: {logo_path}")

    # ---- Title inside the bar (auto-scaling to fit) ----
    title_lines = [
        (project_name or "TO BE CONFIRMED").upper(),
        (project_location or "TO BE CONFIRMED").upper(),
        "SUBMITTAL PACKAGE",
    ]
    sizes, dyn_leading = fit_multiline_text(
        lines=title_lines,
        font_name=FONT_TITLE,
        bar_width=width,
        bar_height=bar_height,
        side_pad=48,
        v_pad=18,
        max_pt=36,
        min_pt=14,
        leading_factor=1.12,
        letter_spacing=0.0,
    )
    draw_centered_stack(
        c,
        x_center=width / 2.0,
        y_center=bar_y + bar_height / 2.0,
        lines=title_lines,
        sizes=sizes,
        font_name=FONT_TITLE,
        color_rgb=(1, 1, 1),
        leading=dyn_leading,
    )

    # ---- Bottom centered lines ----
    c.setFillColorRGB(0, 0, 0)
    bottom_block_y = 140

    role_label  = (party_label or "Recipient").upper()
    company_txt = (party_name or "To Be Confirmed").upper()
    first_line  = f"{role_label}: {company_txt}"

    date_prep_txt = format_mdY(date_prepared, blank="To Be Confirmed").upper()

    lines_bottom = [first_line, f"DATE PREPARED: {date_prep_txt}"]

    # BID DATE handling
    if not bid_date_na:
        if bid_date_tbc or not bid_date:
            lines_bottom.append("BID DATE: TO BE CONFIRMED")
        else:
            lines_bottom.append(f"BID DATE: {format_mdY(bid_date).upper()}")

    draw_centered_stack(
        c,
        x_center=width / 2.0,
        y_center=bottom_block_y,
        lines=lines_bottom,
        sizes=[12] * len(lines_bottom),
        font_name=FONT_TEXT,
        color_rgb=(0, 0, 0),
        leading=18,
    )

    c.showPage()
    c.save()

# ---------- Streamlit UI ----------
st.title("Jomar Valve Submittal Package Builder")
st.caption("Select multiple PDF spec sheets to generate a combined PDF with a custom cover page")

uploaded_files = st.file_uploader("Select PDF spec sheets", type="pdf", accept_multiple_files=True)

# Role selection (mutually-exclusive checkboxes) — no header
selected_role = role_checkbox_group(key_prefix="aud")

# Text input directly under the checkboxes for the name/company
party_name = st.text_input("Company", "")

project_name = st.text_input("Project Name", "")
project_location = st.text_input("Project Location", "")

# Dates
date_prepared = st.date_input("Date Prepared", key="dp_date")
bid_date, bid_date_tbc, bid_date_na = bid_date_picker_with_flags("Bid Date", key="bd")

# Default logo path
# near the top with your imports
from pathlib import Path

# resolve path to the repo folder (works locally and on Streamlit Cloud)
APP_DIR = Path(__file__).parent

# update the filename to match EXACTLY (Linux is case-sensitive)
LOGO_FILENAME = "Jomar Valve Logo Red.png"   # <- change if your file name differs

# if you keep it in an assets/ folder:
default_logo_path = str(APP_DIR / LOGO_FILENAME)

# (or, if the image lives next to your .py file, use this instead)
# default_logo_path = str(APP_DIR / LOGO_FILENAME)

# ---- Reorder UI BEFORE the button ----
ordered_files = []
if uploaded_files:
    labels = [f.name for f in uploaded_files]               # show only names
    ordered_labels = sort_labels(labels, key="file_order")  # drag & drop (or fallback)

    # Map the sorted names back to UploadedFile objects (handles duplicates)
    label_to_idxs = {}
    for i, lbl in enumerate(labels):
        label_to_idxs.setdefault(lbl, []).append(i)
    ordered_files = [uploaded_files[label_to_idxs[lbl].pop(0)] for lbl in ordered_labels]

# ---- Generate ----
if uploaded_files and st.button("Generate Combined PDF"):
    cover_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    make_cover_pdf(
        cover_tmp.name,
        logo_path=default_logo_path,
        project_name=project_name,
        project_location=project_location,
        party_label=selected_role,   # role (may be None)
        party_name=party_name,       # company/name text
        date_prepared=date_prepared,
        bid_date=bid_date,
        bid_date_tbc=bid_date_tbc,
        bid_date_na=bid_date_na,
    )

    final_files = ordered_files or uploaded_files

    merger = PdfMerger()
    merger.append(cover_tmp.name)
    for uf in final_files:
        merger.append(uf)

    out_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    merger.write(out_tmp.name)
    merger.close()

    with open(out_tmp.name, "rb") as f:
        st.download_button(
            "Download Combined PDF",
            f,
            file_name="Combined_Spec_Sheets.pdf",
            mime="application/pdf"
        )
