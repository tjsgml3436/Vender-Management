import streamlit as st
import psycopg2
import pandas as pd
import plotly.express as px
from datetime import date
from streamlit_paste_button import paste_image_button
import io, os, json, base64

st.set_page_config(page_title="해외 하도급 단가 관리", page_icon="🌏", layout="wide")

CURRENCIES = ["KRW", "USD", "JPY", "CNY", "PHP", "VND", "THB", "MYR", "IDR"]
DOMAINS    = ["제조업", "자율주행", "농축산업", "건설업", "반도체", "기타"]
PM_LIST    = ["강대근", "정요찬", "서정화", "오승현", "이선희"]
TASK_TYPES = ["Segmentation", "Bounding Box", "Key Point", "Polyline", "Polygon"]
UNITS      = ["객체 당", "프레임 당"]


# ──────────────────────────────────────────────
# DB
# ──────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(st.secrets["DATABASE_URL"])


def init_db():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS vendors (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE, country TEXT,
            contact_name TEXT, contact_email TEXT, contact_phone TEXT,
            notes TEXT, created_at TIMESTAMPTZ DEFAULT NOW()
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS projects (
            id BIGSERIAL PRIMARY KEY,
            project_name TEXT NOT NULL, domain TEXT,
            epic_url TEXT, task_detail TEXT, sample_image TEXT,
            contract_yn TEXT DEFAULT 'N', contract_status TEXT,
            contract_start_date TEXT, contract_end_date TEXT,
            contract_number TEXT, contract_amount BIGINT,
            drop_reason TEXT, pm TEXT, contracted_vendor_id BIGINT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS vendor_quotes (
            id BIGSERIAL PRIMARY KEY,
            project_id BIGINT NOT NULL,
            vendor_id BIGINT,
            notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS project_tasks (
            id BIGSERIAL PRIMARY KEY,
            quote_id BIGINT,
            project_id BIGINT,
            vendor_id BIGINT,
            task_name TEXT, task_types TEXT,
            unit_price BIGINT DEFAULT 0, currency TEXT DEFAULT 'USD',
            unit TEXT DEFAULT '객체 당',
            quantity BIGINT DEFAULT 0, total_purchase BIGINT DEFAULT 0,
            reference_date TEXT, notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )''')
        for col_sql in [
            "ALTER TABLE projects ADD COLUMN IF NOT EXISTS sample_image TEXT",
            "ALTER TABLE projects ADD COLUMN IF NOT EXISTS contract_status TEXT",
            "ALTER TABLE projects ADD COLUMN IF NOT EXISTS pm TEXT",
            "ALTER TABLE projects ADD COLUMN IF NOT EXISTS contracted_vendor_id BIGINT",
            "ALTER TABLE project_tasks ADD COLUMN IF NOT EXISTS quote_id BIGINT",
            "ALTER TABLE project_tasks ADD COLUMN IF NOT EXISTS task_name TEXT",
            "ALTER TABLE project_tasks ADD COLUMN IF NOT EXISTS quantity BIGINT DEFAULT 0",
            "ALTER TABLE project_tasks ADD COLUMN IF NOT EXISTS total_purchase BIGINT DEFAULT 0",
        ]:
            cur.execute(col_sql)
        conn.commit()
    finally:
        conn.close()


# ──────────────────────────────────────────────
# 조회
# ──────────────────────────────────────────────
@st.cache_data(ttl=30)
def get_vendors():
    conn = get_conn()
    try: return pd.read_sql("SELECT * FROM vendors ORDER BY name", conn)
    finally: conn.close()


@st.cache_data(ttl=30)
def get_projects():
    conn = get_conn()
    try: return pd.read_sql(
        """SELECT id, project_name, domain, epic_url, task_detail, sample_image,
                  contract_yn, contract_status, contract_start_date, contract_end_date,
                  contract_number, contract_amount, drop_reason, pm, contracted_vendor_id,
                  created_at
           FROM projects ORDER BY created_at DESC""", conn)
    finally: conn.close()


@st.cache_data(ttl=30)
def get_all_tasks():
    """전체 TASK (분석용)"""
    conn = get_conn()
    try:
        return pd.read_sql("""
            SELECT
                pt.id, pt.project_id, pt.quote_id,
                p.project_name AS 프로젝트명, p.domain AS 분야,
                p.epic_url AS 에픽URL,
                v.name AS 업체명, v.country AS 국가,
                pt.task_name AS 과업명, pt.task_types AS 과업유형,
                pt.unit_price AS 단가, pt.currency AS 통화,
                pt.unit AS 단위, pt.quantity AS 총작업수량,
                pt.total_purchase AS 총매입액,
                pt.reference_date AS 기준일, pt.notes AS 비고
            FROM project_tasks pt
            JOIN projects p ON pt.project_id = p.id
            LEFT JOIN vendors v ON pt.vendor_id = v.id
            ORDER BY pt.created_at DESC
        """, conn)
    finally: conn.close()


@st.cache_data(ttl=30)
def get_quotes_for_project(project_id):
    """프로젝트의 업체별 견적 목록"""
    conn = get_conn()
    try:
        quotes = pd.read_sql("""
            SELECT vq.id AS quote_id, v.name AS 업체명, v.country AS 국가,
                   vq.notes AS 견적메모
            FROM vendor_quotes vq
            LEFT JOIN vendors v ON vq.vendor_id = v.id
            WHERE vq.project_id = %s
        """, conn, params=(project_id,))
        tasks = pd.read_sql("""
            SELECT pt.quote_id, pt.task_name AS 과업명, pt.task_types AS 과업유형,
                   pt.unit_price AS 단가, pt.currency AS 통화, pt.unit AS 단위,
                   pt.quantity AS 총작업수량, pt.total_purchase AS 총매입액,
                   pt.reference_date AS 기준일, pt.notes AS 비고
            FROM project_tasks pt
            WHERE pt.project_id = %s
        """, conn, params=(project_id,))
        return quotes, tasks
    finally: conn.close()


@st.cache_data(ttl=30)
def get_project_total(project_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(SUM(total_purchase),0) FROM project_tasks WHERE project_id=%s",
            (project_id,)
        )
        r = cur.fetchone()
        return r[0] if r else 0
    finally: conn.close()


@st.cache_data(ttl=30)
def get_vendor_total_for_project(project_id, vendor_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT COALESCE(SUM(pt.total_purchase),0)
               FROM project_tasks pt
               JOIN vendor_quotes vq ON pt.quote_id = vq.id
               WHERE pt.project_id=%s AND vq.vendor_id=%s""",
            (project_id, vendor_id)
        )
        r = cur.fetchone()
        return r[0] if r else 0
    finally: conn.close()


# ──────────────────────────────────────────────
# Excel 내보내기
# ──────────────────────────────────────────────
def _strip_tz(df: pd.DataFrame) -> pd.DataFrame:
    """timezone-aware datetime 컬럼을 naive로 변환 (openpyxl 호환)"""
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            if hasattr(df[col].dt, "tz") and df[col].dt.tz is not None:
                df[col] = df[col].dt.tz_localize(None)
    return df


def build_excel():
    projects = _strip_tz(get_projects())
    tasks    = _strip_tz(get_all_tasks())
    vendors  = _strip_tz(get_vendors())
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        projects.rename(columns={
            "id":"ID","project_name":"프로젝트명","domain":"분야",
            "epic_url":"에픽URL","task_detail":"과업상세",
            "contract_yn":"계약여부","contract_status":"진행상태",
            "contract_start_date":"계약시작일","contract_end_date":"계약종료일",
            "contract_number":"계약번호","contract_amount":"계약금액",
            "drop_reason":"Drop사유","created_at":"등록일"
        }).to_excel(writer, sheet_name="프로젝트 목록", index=False)

        if len(tasks) > 0:
            tasks[["프로젝트명","분야","업체명","국가","과업명","과업유형",
                   "단가","통화","단위","총작업수량","총매입액","기준일","비고"]]\
                .to_excel(writer, sheet_name="TASK 목록", index=False)

        vendors.rename(columns={
            "id":"ID","name":"업체명","country":"국가",
            "contact_name":"담당자","contact_email":"이메일",
            "contact_phone":"연락처","notes":"비고","created_at":"등록일"
        }).to_excel(writer, sheet_name="업체 목록", index=False)
    return output.getvalue()


# ──────────────────────────────────────────────
# 세션 초기화
# ──────────────────────────────────────────────
if "db_initialized" not in st.session_state:
    init_db()
    st.session_state["db_initialized"] = True
if "vendor_count" not in st.session_state:
    st.session_state["vendor_count"] = 1
if "vtask_count_0" not in st.session_state:
    st.session_state["vtask_count_0"] = 1

_PAGE_OPTIONS = ["📊 대시보드", "🏢 업체 관리", "📝 프로젝트 관리", "📈 단가 분석"]

if "page" not in st.session_state:
    st.session_state["page"] = "📊 대시보드"
_nav = st.session_state.pop("nav_to_page", None)
if _nav and _nav in _PAGE_OPTIONS:
    st.session_state["page"] = _nav

with st.sidebar:
    st.title("🌏 해외 하도급")
    st.caption("Enterprise Vision Team")
    st.divider()
    for _opt in _PAGE_OPTIONS:
        _is_active = st.session_state["page"] == _opt
        if st.button(_opt, use_container_width=True,
                     type="primary" if _is_active else "secondary"):
            st.session_state["page"] = _opt
            st.rerun()
    st.divider()
    st.download_button(
        label="📥 엑셀 내보내기",
        data=build_excel(),
        file_name=f"해외하도급_{date.today()}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

page = st.session_state["page"]


# ══════════════════════════════════════════════
# 1. 대시보드
# ══════════════════════════════════════════════
if page == "📊 대시보드":
    st.title("📊 대시보드")
    vendors  = get_vendors()
    projects = get_projects()
    tasks    = get_all_tasks()

    # 계약 완료된 선정 업체 총매입액만 합산
    contracted_total = 0
    if len(projects) > 0:
        for _, _proj in projects.iterrows():
            if _proj.get("contract_yn") == "Y":
                _cvid = _proj.get("contracted_vendor_id")
                if _cvid and not pd.isna(_cvid):
                    contracted_total += get_vendor_total_for_project(int(_proj["id"]), int(_cvid))

    # 등록 TASK 건수: 프로젝트×과업명 기준 중복 제거
    unique_task_count = len(tasks.drop_duplicates(subset=["프로젝트명","과업명"])) if len(tasks) > 0 else 0

    c1,c2,c3,c4,c5 = st.columns(5)
    c1.metric("등록 업체", len(vendors))
    c2.metric("등록 프로젝트", len(projects))
    c3.metric("등록 TASK", unique_task_count)
    c4.metric("계약 완료", len(projects[projects["contract_yn"]=="Y"]) if len(projects)>0 else 0)
    c5.metric("총 매입액 합계", f"{int(contracted_total):,}원")

    st.divider()
    if len(tasks)==0:
        st.info("아직 등록된 데이터가 없습니다.")
    else:
        cl, cr = st.columns(2)
        with cl:
            st.subheader("분야별 프로젝트 수")
            dc = projects.groupby("domain").size().reset_index(name="건수")
            fig = px.bar(dc, x="domain", y="건수", color="건수",
                         color_continuous_scale="Blues", labels={"domain":"분야"})
            fig.update_layout(coloraxis_showscale=False, height=300)
            st.plotly_chart(fig, use_container_width=True)
        with cr:
            st.subheader("과업유형별 등록 건수")
            # 프로젝트×과업명 기준 중복 제거 후 과업유형 집계
            unique_tasks_df = tasks.drop_duplicates(subset=["프로젝트명","과업명"])
            rows = [t.strip() for _, r in unique_tasks_df.iterrows()
                    for t in str(r["과업유형"]).split(",")]
            tc = pd.DataFrame({"과업유형": rows})["과업유형"].value_counts().reset_index()
            tc.columns = ["과업유형", "건수"]
            fig2 = px.pie(tc, names="과업유형", values="건수", hole=0.4)
            fig2.update_layout(height=300)
            st.plotly_chart(fig2, use_container_width=True)

        st.subheader("최근 등록 프로젝트")
        hdr0, hdr1, hdr2, hdr3, hdr4 = st.columns([3,2,1,1,2])
        hdr0.markdown("**프로젝트명**"); hdr1.markdown("**분야**")
        hdr2.markdown("**계약여부**"); hdr3.markdown("**진행상태**"); hdr4.markdown("**등록일**")
        st.divider()
        for _, _proj in projects.head(10).iterrows():
            _cyn = _proj.get("contract_yn","N")
            _cst = _proj.get("contract_status","")
            _status = "🟢 계약" if _cyn=="Y" else ("🔴 Drop" if _cst=="Drop" else "🟡 Holding")
            pc0, pc1, pc2, pc3, pc4 = st.columns([3,2,1,1,2])
            if pc0.button(_proj["project_name"], key=f"dash_nav_{_proj['id']}", use_container_width=True):
                st.session_state["nav_to_page"] = "📝 프로젝트 관리"
                st.session_state["scroll_to_project"] = int(_proj["id"])
                st.rerun()
            pc1.write(_proj.get("domain",""))
            pc2.write(_cyn)
            pc3.write(_status)
            pc4.write(str(_proj.get("created_at",""))[:10])


# ══════════════════════════════════════════════
# 2. 업체 관리
# ══════════════════════════════════════════════
elif page == "🏢 업체 관리":
    st.title("🏢 업체 관리")
    tab1, tab2 = st.tabs(["업체 목록", "업체 등록"])

    with tab1:
        vendors = get_vendors()
        if len(vendors)==0:
            st.info("등록된 업체가 없습니다.")
        else:
            _, btn_col = st.columns([8,2])
            with btn_col:
                if st.button("✏️ 업체 수정", use_container_width=True):
                    st.session_state["show_edit_vendor"] = not st.session_state.get("show_edit_vendor", False)
            st.dataframe(vendors[["name","country","contact_name","contact_email","contact_phone","notes","created_at"]].rename(columns={
                "name":"업체명","country":"국가","contact_name":"담당자",
                "contact_email":"이메일","contact_phone":"연락처","notes":"비고","created_at":"등록일"
            }), use_container_width=True, hide_index=True)

            if st.session_state.get("show_edit_vendor", False):
                st.divider()
                st.subheader("업체 정보 수정")
                sel = st.selectbox("수정할 업체", ["수정할 업체를 선택하세요"]+vendors["name"].tolist(), key="edit_vendor_select")
                if sel != "수정할 업체를 선택하세요":
                    row = vendors[vendors["name"]==sel].iloc[0]
                    with st.form("edit_vendor_form"):
                        c1,c2 = st.columns(2)
                        nn = c1.text_input("업체명 *", value=row["name"])
                        nc = c2.text_input("국가 *", value=row["country"] or "")
                        nct= c1.text_input("담당자명", value=row["contact_name"] or "")
                        ne = c2.text_input("이메일", value=row["contact_email"] or "")
                        np = c1.text_input("연락처", value=row["contact_phone"] or "")
                        nno= st.text_area("비고", value=row["notes"] or "")
                        if st.form_submit_button("저장", type="primary"):
                            if not nn or not nc:
                                st.error("업체명과 국가는 필수입니다.")
                            else:
                                conn = get_conn()
                                try:
                                    cur = conn.cursor()
                                    cur.execute("UPDATE vendors SET name=%s,country=%s,contact_name=%s,contact_email=%s,contact_phone=%s,notes=%s WHERE id=%s",
                                                 (nn,nc,nct,ne,np,nno,int(row["id"])))
                                    conn.commit()
                                    st.cache_data.clear()
                                    st.success(f"'{nn}' 수정 완료")
                                    st.session_state["show_edit_vendor"] = False
                                    st.rerun()
                                except psycopg2.IntegrityError:
                                    st.error("이미 동일한 업체명이 존재합니다.")
                                finally: conn.close()

    with tab2:
        st.subheader("신규 업체 등록")
        with st.form("add_vendor"):
            c1,c2 = st.columns(2)
            name=c1.text_input("업체명 *"); country=c2.text_input("국가 *")
            cname=c1.text_input("담당자명"); email=c2.text_input("이메일")
            phone=c1.text_input("연락처"); notes=st.text_area("비고")
            if st.form_submit_button("등록", type="primary"):
                if not name or not country: st.error("업체명과 국가는 필수입니다.")
                else:
                    conn = get_conn()
                    try:
                        cur = conn.cursor()
                        cur.execute("INSERT INTO vendors (name,country,contact_name,contact_email,contact_phone,notes) VALUES (%s,%s,%s,%s,%s,%s)",
                                     (name,country,cname,email,phone,notes))
                        conn.commit(); st.cache_data.clear(); st.success(f"'{name}' 등록 완료"); st.rerun()
                    except psycopg2.IntegrityError: st.error("이미 동일한 업체명이 존재합니다.")
                    finally: conn.close()

        st.divider()
        st.subheader("업체 삭제")
        vfd = get_vendors()
        if len(vfd)>0:
            vtd = st.selectbox("삭제할 업체", ["삭제할 업체를 선택하세요"]+vfd["name"].tolist(), key="del_vendor")
            if st.button("삭제", type="secondary"):
                if vtd=="삭제할 업체를 선택하세요": st.warning("업체를 선택해주세요."); st.stop()
                conn = get_conn()
                try:
                    cur = conn.cursor()
                    cur.execute("SELECT id FROM vendors WHERE name=%s", (vtd,))
                    row = cur.fetchone()
                    vid = row[0]
                    cur.execute("SELECT COUNT(*) FROM project_tasks WHERE vendor_id=%s", (vid,))
                    cnt = cur.fetchone()[0]
                    if cnt>0: st.error(f"연결된 TASK {cnt}건이 있어 삭제 불가합니다.")
                    else:
                        cur.execute("DELETE FROM vendors WHERE id=%s", (vid,))
                        conn.commit(); st.cache_data.clear(); st.success("삭제 완료"); st.rerun()
                finally: conn.close()


# ══════════════════════════════════════════════
# 3. 프로젝트 등록
# ══════════════════════════════════════════════
elif page == "📝 프로젝트 관리":
    st.title("📝 프로젝트 관리")
    tab1, tab2 = st.tabs(["프로젝트 목록", "프로젝트 등록"])

    # ── 목록 ──────────────────────────────────
    with tab1:
        projects = get_projects()
        if len(projects)==0:
            st.info("등록된 프로젝트가 없습니다.")
        else:
            scroll_pid = st.session_state.get("scroll_to_project")
            for _, proj in projects.iterrows():
                cyn = proj.get("contract_yn","N")
                cst = proj.get("contract_status","")
                badge = "🟢 계약" if cyn=="Y" else ("🔴 Drop" if cst=="Drop" else "🟡 Holding")
                contracted_vid = proj.get("contracted_vendor_id")
                if cyn == "Y" and contracted_vid and not pd.isna(contracted_vid):
                    total_p = get_vendor_total_for_project(int(proj["id"]), int(contracted_vid))
                    total_display = f"{int(total_p):,}원"
                else:
                    total_p = 0
                    total_display = "-"

                pm_label = proj.get("pm") or "-"
                _auto_expand = (scroll_pid is not None and scroll_pid == int(proj["id"]))
                with st.expander(f"**{proj['project_name']}**  |  {proj['domain']}  |  PM {pm_label}  |  {badge}  |  총매입액 {total_display}  |  {str(proj['created_at'])[:10]}", expanded=_auto_expand):
                    img_col, info_col = st.columns([1,3])
                    with img_col:
                        img_f = proj.get("sample_image")
                        img_list = []
                        if img_f:
                            try:
                                img_list = json.loads(img_f) if str(img_f).startswith("[") else [img_f]
                            except Exception:
                                img_list = [img_f]
                        if img_list:
                            def _show_img(data_uri):
                                try:
                                    _, b64data = data_uri.split(",", 1)
                                    return io.BytesIO(base64.b64decode(b64data))
                                except Exception:
                                    return None
                            if len(img_list) == 1:
                                _img = _show_img(img_list[0])
                                if _img: st.image(_img, use_container_width=True)
                            else:
                                gcols = st.columns(min(len(img_list), 3))
                                for gi, gf in enumerate(img_list):
                                    _img = _show_img(gf)
                                    label = "대표" if gi == 0 else f"{gi+1}번째"
                                    if _img: gcols[gi % 3].image(_img, caption=label, use_container_width=True)
                        else:
                            st.markdown("*(이미지 없음)*")
                    with info_col:
                        cc1,cc2,cc3,cc4 = st.columns(4)
                        cc1.markdown(f"**분야:** {proj['domain']}")
                        cc2.markdown(f"**담당 PM:** {pm_label}")
                        cc3.markdown(f"**상태:** {badge}")
                        cc4.markdown(f"**총 매입액:** {total_display}")
                        if proj.get("epic_url"):
                            st.markdown(f"**에픽티켓:** [{proj['epic_url']}]({proj['epic_url']})")
                        if proj.get("task_detail"):
                            st.markdown(f"**과업 상세:** {proj['task_detail']}")

                    # 계약 정보
                    if cyn=="Y":
                        st.markdown("---")
                        dc1,dc2,dc3,dc4 = st.columns(4)
                        dc1.markdown(f"**계약시작일:** {proj.get('contract_start_date') or '-'}")
                        dc2.markdown(f"**계약종료일:** {proj.get('contract_end_date') or '-'}")
                        dc3.markdown(f"**계약번호:** {proj.get('contract_number') or '-'}")
                        amt = proj.get("contract_amount")
                        dc4.markdown(f"**계약금액:** {int(amt):,}원" if amt else "**계약금액:** -")
                    elif cst=="Drop" and proj.get("drop_reason"):
                        st.markdown(f"**Drop 사유:** {proj.get('drop_reason')}")

                    # 업체별 견적
                    quotes, qtasks = get_quotes_for_project(int(proj["id"]))
                    if len(quotes)>0:
                        st.markdown("---")
                        st.markdown("**업체별 견적**")
                        for _, q in quotes.iterrows():
                            vtotal = qtasks[qtasks["quote_id"]==q["quote_id"]]["총매입액"].sum()
                            st.markdown(f"**{q['업체명']} ({q['국가']})** — 총매입액 {int(vtotal):,}원")
                            qt = qtasks[qtasks["quote_id"]==q["quote_id"]]
                            if len(qt)>0:
                                _NUM_FMT = {"단가":"{:,.0f}", "총작업수량":"{:,.0f}", "총매입액":"{:,.0f}"}
                                st.dataframe(
                                    qt[["과업명","과업유형","단가","통화","단위","총작업수량","총매입액","기준일","비고"]].style.format(_NUM_FMT),
                                    use_container_width=True, hide_index=True
                                )

                        # 단가 비교 차트 (업체 2개 이상이고 단가 데이터 있을 때)
                        if len(quotes) > 1 and len(qtasks) > 0 and qtasks["단가"].sum() > 0:
                            # qtasks에 업체명 붙이기 (quote_id → 업체명)
                            _qtasks_named = qtasks.merge(
                                quotes[["quote_id","업체명"]], on="quote_id", how="left"
                            )
                            st.markdown("---")
                            st.markdown("**하도급 단가 비교**")
                            _piv = _qtasks_named.pivot_table(
                                index="과업명", columns="업체명", values="단가", aggfunc="mean"
                            ).reset_index()
                            _piv_fmt = {c: "{:,.0f}" for c in _piv.columns if c != "과업명"}
                            st.dataframe(_piv.style.format(_piv_fmt), use_container_width=True, hide_index=True)

                            _fig_p = px.bar(
                                _qtasks_named.dropna(subset=["업체명","단가"]),
                                x="과업명", y="단가", color="업체명",
                                barmode="group", text="단가",
                                labels={"단가":"단가","과업명":"과업","업체명":"업체"}
                            )
                            _fig_p.update_traces(texttemplate="%{text:,}", textposition="outside")
                            _fig_p.update_layout(height=320, showlegend=True, margin=dict(t=20))
                            st.plotly_chart(_fig_p, use_container_width=True)

                    st.markdown("---")
                    _btn_edit, _btn_del = st.columns([1,1])
                    if _btn_edit.button("✏️ 프로젝트 수정", key=f"edit_btn_{proj['id']}", use_container_width=True):
                        _ek = f"edit_proj_{proj['id']}"
                        st.session_state[_ek] = not st.session_state.get(_ek, False)
                        st.rerun()
                    if _btn_del.button("🗑️ 프로젝트 삭제", key=f"del_proj_{proj['id']}", use_container_width=True):
                        conn = get_conn()
                        try:
                            cur = conn.cursor()
                            cur.execute("DELETE FROM project_tasks WHERE project_id=%s", (int(proj["id"]),))
                            cur.execute("DELETE FROM vendor_quotes WHERE project_id=%s", (int(proj["id"]),))
                            cur.execute("DELETE FROM projects WHERE id=%s", (int(proj["id"]),))
                            conn.commit(); st.cache_data.clear(); st.success("삭제 완료"); st.rerun()
                        finally: conn.close()

                    if st.session_state.get(f"edit_proj_{proj['id']}", False):
                        st.markdown("#### ✏️ 프로젝트 수정")
                        _vendors_e = get_vendors()
                        _vlist_e = _vendors_e["name"].tolist() if len(_vendors_e) > 0 else []
                        with st.form(f"edit_proj_form_{proj['id']}"):
                            ep1, ep2, ep3 = st.columns(3)
                            e_name   = ep1.text_input("프로젝트명 *", value=proj.get("project_name") or "")
                            e_domain = ep2.selectbox("분야 *", DOMAINS,
                                index=DOMAINS.index(proj["domain"]) if proj.get("domain") in DOMAINS else 0)
                            e_pm     = ep3.selectbox("담당 PM *", PM_LIST,
                                index=PM_LIST.index(proj["pm"]) if proj.get("pm") in PM_LIST else 0)
                            ep4, ep5 = st.columns(2)
                            e_epic   = ep4.text_input("에픽티켓 URL", value=proj.get("epic_url") or "")
                            e_detail = ep5.text_area("과업 상세", value=proj.get("task_detail") or "")

                            e_cyn = st.radio("계약 여부", ["N","Y"], horizontal=True,
                                index=0 if proj.get("contract_yn","N")=="N" else 1,
                                key=f"e_cyn_{proj['id']}")
                            e_contracted_vid = None
                            if e_cyn == "Y":
                                ea1,ea2 = st.columns(2)
                                e_cstart = ea1.text_input("계약 시작일", value=proj.get("contract_start_date") or "")
                                e_cend   = ea2.text_input("계약 종료일", value=proj.get("contract_end_date") or "")
                                eb1,eb2 = st.columns(2)
                                e_cnum   = eb1.text_input("계약번호", value=proj.get("contract_number") or "")
                                e_camt   = eb2.number_input("계약금액 (KRW)", min_value=0, step=1,
                                    value=int(proj["contract_amount"]) if proj.get("contract_amount") else 0)
                                _cur_cv  = ""
                                if proj.get("contracted_vendor_id") and not pd.isna(proj["contracted_vendor_id"]):
                                    _cv_row = _vendors_e[_vendors_e["id"]==int(proj["contracted_vendor_id"])]
                                    _cur_cv = _cv_row.iloc[0]["name"] if len(_cv_row) > 0 else ""
                                e_cv_sel = st.selectbox("선정 업체", ["선택하세요"] + _vlist_e,
                                    index=(["선택하세요"]+_vlist_e).index(_cur_cv) if _cur_cv in _vlist_e else 0)
                                e_cst, e_drop = None, None
                            else:
                                e_cst  = st.radio("진행 상태", ["Holding","Drop"], horizontal=True,
                                    index=0 if proj.get("contract_status","Holding")!="Drop" else 1,
                                    key=f"e_cst_{proj['id']}")
                                e_drop = st.text_area("Drop 사유", value=proj.get("drop_reason") or "") if e_cst=="Drop" else None
                                e_cstart = e_cend = e_cnum = e_cv_sel = None
                                e_camt = 0

                            if st.form_submit_button("💾 저장", type="primary"):
                                if not e_name:
                                    st.error("프로젝트명은 필수입니다.")
                                else:
                                    conn = get_conn()
                                    try:
                                        cur = conn.cursor()
                                        if e_cyn == "Y" and e_cv_sel and e_cv_sel != "선택하세요":
                                            cur.execute("SELECT id FROM vendors WHERE name=%s", (e_cv_sel,))
                                            r = cur.fetchone()
                                            e_contracted_vid = r[0] if r else None
                                        cur.execute(
                                            """UPDATE projects SET
                                               project_name=%s, domain=%s, pm=%s,
                                               epic_url=%s, task_detail=%s,
                                               contract_yn=%s, contract_status=%s,
                                               contract_start_date=%s, contract_end_date=%s,
                                               contract_number=%s, contract_amount=%s,
                                               drop_reason=%s, contracted_vendor_id=%s
                                               WHERE id=%s""",
                                            (e_name, e_domain, e_pm,
                                             e_epic, e_detail,
                                             e_cyn, e_cst,
                                             e_cstart, e_cend,
                                             e_cnum, e_camt if e_cyn=="Y" else None,
                                             e_drop, e_contracted_vid,
                                             int(proj["id"]))
                                        )
                                        conn.commit()
                                        st.cache_data.clear()
                                        st.session_state[f"edit_proj_{proj['id']}"] = False
                                        st.success(f"'{e_name}' 수정 완료")
                                        st.rerun()
                                    finally: conn.close()

    # ── 등록 ──────────────────────────────────
    with tab2:
        vendors = get_vendors()
        vendor_list = vendors["name"].tolist() if len(vendors)>0 else []

        # 프로젝트 정보
        st.subheader("프로젝트 정보")
        pc1,pc2,pc3 = st.columns(3)
        project_name = pc1.text_input("프로젝트명 *", key="proj_name")
        domain       = pc2.selectbox("분야 *", ["분야를 선택하세요"]+DOMAINS, key="proj_domain")
        pm           = pc3.selectbox("담당 PM *", ["PM을 선택하세요"]+PM_LIST, key="proj_pm")
        pc4,pc5 = st.columns(2)
        epic_url    = pc4.text_input("에픽티켓 URL", key="proj_epic_url", placeholder="https://jira....")
        task_detail = pc5.text_area("과업 상세", key="proj_task_detail", placeholder="과업에 대한 전반적인 설명")

        st.markdown("**샘플 이미지 (최대 5장, 첫 번째 이미지가 대표 이미지로 사용됩니다)**")
        it1, it2 = st.tabs(["📁 파일 업로드", "📋 클립보드 붙여넣기"])
        with it1:
            uploaded_imgs = st.file_uploader(
                "이미지 선택 (최대 5장)", type=["png","jpg","jpeg","webp"],
                key="proj_sample_image", label_visibility="collapsed",
                accept_multiple_files=True
            )
            if uploaded_imgs:
                if len(uploaded_imgs) > 5:
                    st.warning("최대 5장까지만 업로드됩니다. 처음 5장만 저장됩니다.")
                    uploaded_imgs = uploaded_imgs[:5]
                cols = st.columns(len(uploaded_imgs))
                for i, img in enumerate(uploaded_imgs):
                    label = "대표" if i == 0 else f"{i+1}번째"
                    cols[i].image(img, caption=label, use_container_width=True)
        with it2:
            st.caption("이미지를 복사한 뒤 아래 버튼을 클릭하세요. (1장, 대표 이미지로 저장)")
            pr = paste_image_button("📋 붙여넣기", key="proj_paste_image")
            if pr and pr.image_data is not None:
                st.session_state["pasted_image"] = pr.image_data
            if st.session_state.get("pasted_image"):
                st.image(st.session_state["pasted_image"], width=280)

        # 계약 정보
        st.divider()
        st.subheader("계약 정보")
        contract_yn = st.radio("계약 여부", ["N","Y"], horizontal=True, key="proj_contract_yn")
        if contract_yn=="Y":
            ca1,ca2 = st.columns(2)
            ca1.date_input("계약 시작일", key="proj_contract_start", value=date.today())
            ca2.date_input("계약 종료일", key="proj_contract_end", value=date.today())
            cb1,cb2 = st.columns(2)
            cb1.text_input("계약번호", key="proj_contract_number", placeholder="CT-2026-001")
            cb2.number_input("계약금액 (KRW)", min_value=0, step=1, key="proj_contract_amount")
            st.selectbox("선정 업체 *", ["선택하세요"] + vendor_list, key="proj_contracted_vendor")
        else:
            cstatus = st.radio("진행 상태", ["Holding","Drop"], horizontal=True, key="proj_contract_status")
            if cstatus=="Drop":
                st.text_area("Drop 사유", key="proj_drop_reason", placeholder="진행되지 않은 사유")

        # 하도급 업체별 견적
        st.divider()
        st.subheader("하도급 업체별 견적")
        st.caption("업체를 선택하고 해당 업체의 TASK를 입력하세요. 업체 추가 버튼으로 여러 업체 견적을 비교할 수 있습니다.")

        if len(vendor_list)==0:
            st.warning("먼저 업체를 등록해주세요.")
        else:
            add_v_col, _ = st.columns([2,8])
            with add_v_col:
                if st.button("+ 업체 추가", use_container_width=True):
                    vc = st.session_state["vendor_count"]
                    st.session_state["vendor_count"] = vc + 1
                    # 첫 번째 업체의 과업 구조(과업명, 과업유형, 총작업수량, 단위, 기준일) 복사
                    tc0 = st.session_state.get("vtask_count_0", 1)
                    st.session_state[f"vtask_count_{vc}"] = tc0
                    for t in range(tc0):
                        for k in ["tname", "tt", "tq", "tu", "td"]:
                            st.session_state[f"{k}_{vc}_{t}"] = st.session_state.get(f"{k}_0_{t}")
                    st.rerun()

            for v in range(st.session_state["vendor_count"]):
                if f"vtask_count_{v}" not in st.session_state:
                    st.session_state[f"vtask_count_{v}"] = 1

                st.markdown(f"### 업체 {v+1}")
                vh1, vh2 = st.columns([3,1])
                vh1.selectbox(
                    "담당 업체 선택 *",
                    ["업체를 선택하세요"] + vendor_list,
                    key=f"vendor_sel_{v}"
                )
                with vh2:
                    if st.session_state["vendor_count"] > 1:
                        if st.button(f"✕ 업체 {v+1} 제거", key=f"del_vendor_group_{v}"):
                            for j in range(v, st.session_state["vendor_count"]-1):
                                st.session_state[f"vendor_sel_{j}"] = st.session_state.get(f"vendor_sel_{j+1}", "업체를 선택하세요")
                                st.session_state[f"vtask_count_{j}"] = st.session_state.get(f"vtask_count_{j+1}", 1)
                                for t in range(st.session_state.get(f"vtask_count_{j+1}", 1)):
                                    for k in ["tname","tt","tp","tc","tu","td","tq","tpur","tn"]:
                                        st.session_state[f"{k}_{j}_{t}"] = st.session_state.get(f"{k}_{j+1}_{t}")
                            st.session_state["vendor_count"] -= 1
                            st.rerun()

                # TASK 목록
                tc = st.session_state[f"vtask_count_{v}"]
                for t in range(tc):
                    with st.container():
                        st.markdown(f"**TASK {t+1}**")
                        ta1, ta2 = st.columns(2)
                        ta1.text_input("과업명 *", key=f"tname_{v}_{t}", placeholder="예: 차량 바운딩박스")
                        ta2.multiselect("과업유형 * (중복선택 가능)", TASK_TYPES, key=f"tt_{v}_{t}")

                        tb1,tb2,tb3,tb4 = st.columns(4)
                        tb1.number_input("단가", min_value=0, step=1, key=f"tp_{v}_{t}")
                        tb2.selectbox("통화", CURRENCIES, key=f"tc_{v}_{t}")
                        tb3.selectbox("단위", UNITS, key=f"tu_{v}_{t}")
                        tb4.date_input("기준일", value=date.today(), key=f"td_{v}_{t}")

                        tq1,tq2 = st.columns(2)
                        tq1.number_input("총 작업 수량", min_value=0, step=1, key=f"tq_{v}_{t}")
                        tq2.number_input("총 매입액", min_value=0, step=1, key=f"tpur_{v}_{t}")
                        st.text_input("비고", key=f"tn_{v}_{t}", placeholder="추가 메모")

                        if tc > 1:
                            if st.button(f"✕ TASK {t+1} 삭제", key=f"del_task_{v}_{t}"):
                                for j in range(t, tc-1):
                                    for k in ["tname","tt","tp","tc","tu","td","tq","tpur","tn"]:
                                        st.session_state[f"{k}_{v}_{j}"] = st.session_state.get(f"{k}_{v}_{j+1}")
                                st.session_state[f"vtask_count_{v}"] -= 1
                                st.rerun()

                        if t < tc-1: st.markdown("---")

                add_t_col, _ = st.columns([2,8])
                with add_t_col:
                    if st.button(f"+ TASK 추가", key=f"add_task_{v}", use_container_width=True):
                        st.session_state[f"vtask_count_{v}"] += 1
                        st.rerun()

                # 업체별 소계
                vendor_sub = sum(st.session_state.get(f"tpur_{v}_{t}", 0) or 0
                                 for t in range(st.session_state[f"vtask_count_{v}"]))
                st.markdown(f"**업체 {v+1} 소계: {int(vendor_sub):,}원**")
                st.divider()

        # 전체 합계
        all_total = sum(
            sum(st.session_state.get(f"tpur_{v}_{t}", 0) or 0
                for t in range(st.session_state.get(f"vtask_count_{v}", 1)))
            for v in range(st.session_state["vendor_count"])
        )
        st.info(f"**전체 총 매입액 합계: {int(all_total):,}원**")

        # ── 실시간 단가 비교표 ──────────────────────
        _vc = st.session_state["vendor_count"]
        _cmp_rows = []
        for _v in range(_vc):
            _vname = st.session_state.get(f"vendor_sel_{_v}", "업체를 선택하세요")
            if _vname == "업체를 선택하세요":
                _vname = f"업체 {_v+1}"
            _tc = st.session_state.get(f"vtask_count_{_v}", 1)
            for _t in range(_tc):
                _tname = (st.session_state.get(f"tname_{_v}_{_t}") or "").strip() or f"TASK {_t+1}"
                _price = int(st.session_state.get(f"tp_{_v}_{_t}") or 0)
                _cur   = st.session_state.get(f"tc_{_v}_{_t}") or ""
                _unit  = st.session_state.get(f"tu_{_v}_{_t}") or ""
                _tpur  = int(st.session_state.get(f"tpur_{_v}_{_t}") or 0)
                _cmp_rows.append({"업체": _vname, "과업명": _tname,
                                   "단가": _price, "통화": _cur,
                                   "단위": _unit, "총매입액": _tpur})

        if _cmp_rows:
            _cmp_df = pd.DataFrame(_cmp_rows)
            st.divider()
            st.subheader("📊 업체별 단가 비교 (입력 중 미리보기)")

            # 피벗 테이블: 과업명 × 업체 → 단가
            _pivot = _cmp_df.pivot_table(
                index=["과업명","단위","통화"], columns="업체",
                values="단가", aggfunc="mean"
            ).reset_index()
            _pivot_fmt = {c: "{:,.0f}" for c in _pivot.columns if c not in ["과업명","단위","통화"]}
            st.dataframe(_pivot.style.format(_pivot_fmt), use_container_width=True, hide_index=True)

            # 바 차트 (단가)
            if _cmp_df["단가"].sum() > 0:
                _fig_cmp = px.bar(
                    _cmp_df, x="과업명", y="단가", color="업체",
                    barmode="group", text="단가",
                    title="과업별 업체 단가 비교",
                    labels={"단가": "단가", "과업명": "과업"}
                )
                _fig_cmp.update_traces(texttemplate="%{text:,}", textposition="outside")
                _fig_cmp.update_layout(height=350, showlegend=True)
                st.plotly_chart(_fig_cmp, use_container_width=True)

            # 총매입액 비교
            if _cmp_df["총매입액"].sum() > 0:
                _tot_df = _cmp_df.groupby("업체")["총매입액"].sum().reset_index()
                _fig_tot = px.bar(
                    _tot_df, x="업체", y="총매입액", color="업체",
                    text="총매입액", title="업체별 총 매입액 비교"
                )
                _fig_tot.update_traces(texttemplate="%{text:,}원", textposition="outside")
                _fig_tot.update_layout(height=300, showlegend=False)
                st.plotly_chart(_fig_tot, use_container_width=True)

        # 저장
        if st.button("💾 프로젝트 저장", type="primary", use_container_width=True):
            pname = st.session_state.get("proj_name","").strip()
            pdom  = st.session_state.get("proj_domain","분야를 선택하세요")

            if not pname:
                st.error("프로젝트명은 필수입니다.")
            elif pdom=="분야를 선택하세요":
                st.error("분야를 선택해주세요.")
            elif st.session_state.get("proj_pm","PM을 선택하세요")=="PM을 선택하세요":
                st.error("담당 PM을 선택해주세요.")
            elif len(vendor_list)==0:
                st.error("업체를 먼저 등록해주세요.")
            else:
                # 유효성 검사
                vendor_data = []
                valid = True
                for v in range(st.session_state["vendor_count"]):
                    vname = st.session_state.get(f"vendor_sel_{v}","업체를 선택하세요")
                    if vname=="업체를 선택하세요":
                        st.error(f"업체 {v+1}: 업체를 선택해주세요."); valid=False; break
                    tasks_data = []
                    for t in range(st.session_state.get(f"vtask_count_{v}",1)):
                        tname = st.session_state.get(f"tname_{v}_{t}","").strip()
                        types = st.session_state.get(f"tt_{v}_{t}",[])
                        if not tname:
                            st.error(f"업체 {v+1} / TASK {t+1}: 과업명은 필수입니다."); valid=False; break
                        if not types:
                            st.error(f"업체 {v+1} / TASK {t+1}: 과업유형을 선택해주세요."); valid=False; break
                        tasks_data.append({
                            "task_name":     tname,
                            "task_types":    ",".join(types),
                            "unit_price":    st.session_state.get(f"tp_{v}_{t}", 0),
                            "currency":      st.session_state.get(f"tc_{v}_{t}", "USD"),
                            "unit":          st.session_state.get(f"tu_{v}_{t}", "건당"),
                            "ref_date":      str(st.session_state.get(f"td_{v}_{t}", date.today())),
                            "quantity":      st.session_state.get(f"tq_{v}_{t}", 0),
                            "total_purchase":st.session_state.get(f"tpur_{v}_{t}", 0),
                            "notes":         st.session_state.get(f"tn_{v}_{t}", ""),
                        })
                    if not valid: break
                    vendor_data.append({"vendor_name": vname, "tasks": tasks_data})

                if valid:
                    cyn = st.session_state.get("proj_contract_yn","N")
                    cst = st.session_state.get("proj_contract_status","Holding") if cyn=="N" else None
                    contracted_vname = st.session_state.get("proj_contracted_vendor","선택하세요") if cyn=="Y" else "선택하세요"
                    if cyn=="Y" and contracted_vname=="선택하세요":
                        st.error("선정 업체를 선택해주세요."); valid=False

                if valid:
                    # 이미지 저장 (base64 인코딩)
                    img_filename = None
                    upls = st.session_state.get("proj_sample_image") or []
                    pst  = st.session_state.get("pasted_image")

                    saved = []
                    if upls:
                        for f_obj in list(upls)[:5]:
                            ext = f_obj.name.rsplit(".", 1)[-1].lower()
                            b64 = base64.b64encode(f_obj.getbuffer()).decode()
                            saved.append(f"data:image/{ext};base64,{b64}")
                    if not saved and pst:
                        buf = io.BytesIO()
                        pst.save(buf, format="PNG")
                        b64 = base64.b64encode(buf.getvalue()).decode()
                        saved.append(f"data:image/png;base64,{b64}")
                    img_filename = json.dumps(saved) if saved else None

                    conn = get_conn()
                    try:
                        # 선정 업체 ID 조회
                        contracted_vid = None
                        if cyn=="Y" and contracted_vname != "선택하세요":
                            cur = conn.cursor()
                            cur.execute("SELECT id FROM vendors WHERE name=%s", (contracted_vname,))
                            vid_row = cur.fetchone()
                            contracted_vid = vid_row[0] if vid_row else None

                        cur = conn.cursor()
                        cur.execute(
                            """INSERT INTO projects
                               (project_name,domain,epic_url,task_detail,sample_image,
                                contract_yn,contract_status,contract_start_date,contract_end_date,
                                contract_number,contract_amount,drop_reason,pm,contracted_vendor_id)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                            (pname, pdom,
                             st.session_state.get("proj_epic_url",""),
                             st.session_state.get("proj_task_detail",""),
                             img_filename, cyn, cst,
                             str(st.session_state.get("proj_contract_start","")) if cyn=="Y" else None,
                             str(st.session_state.get("proj_contract_end","")) if cyn=="Y" else None,
                             st.session_state.get("proj_contract_number","") if cyn=="Y" else None,
                             st.session_state.get("proj_contract_amount",0) if cyn=="Y" else None,
                             st.session_state.get("proj_drop_reason","") if (cyn=="N" and cst=="Drop") else None,
                             st.session_state.get("proj_pm",""),
                             contracted_vid,
                            )
                        )
                        project_id = cur.fetchone()[0]

                        for vd in vendor_data:
                            cur2 = conn.cursor()
                            cur2.execute("SELECT id FROM vendors WHERE name=%s", (vd["vendor_name"],))
                            vid_row = cur2.fetchone()
                            vid = vid_row[0] if vid_row else None
                            cur2.execute(
                                "INSERT INTO vendor_quotes (project_id, vendor_id) VALUES (%s,%s) RETURNING id",
                                (project_id, vid)
                            )
                            quote_id = cur2.fetchone()[0]
                            for t in vd["tasks"]:
                                cur2.execute(
                                    """INSERT INTO project_tasks
                                       (quote_id,project_id,vendor_id,task_name,task_types,
                                        unit_price,currency,unit,quantity,total_purchase,reference_date,notes)
                                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                                    (quote_id, project_id, vid,
                                     t["task_name"], t["task_types"], t["unit_price"],
                                     t["currency"], t["unit"], t["quantity"],
                                     t["total_purchase"], t["ref_date"], t["notes"])
                                )
                        conn.commit()
                        st.cache_data.clear()
                        st.success(f"'{pname}' 저장 완료 (업체 {len(vendor_data)}개, 총 매입액 {int(all_total):,}원)")
                        # 초기화
                        st.session_state["vendor_count"] = 1
                        st.session_state["vtask_count_0"] = 1
                        st.session_state.pop("pasted_image", None)
                        st.rerun()
                    finally: conn.close()


# ══════════════════════════════════════════════
# 4. 단가 분석
# ══════════════════════════════════════════════
elif page == "📈 단가 분석":
    st.title("📈 단가 분석")
    tasks    = get_all_tasks()
    projects = get_projects()

    if len(tasks)==0:
        st.info("분석할 데이터가 없습니다.")
    else:
        tab1, tab2, tab3, tab4 = st.tabs([
            "🔍 프로젝트별 업체 비교",
            "📊 업체별 평균 단가",
            "📋 과업유형별 분석",
            "📄 원본 데이터"
        ])

        # ── Tab1: 프로젝트별 업체 비교 ──────
        with tab1:
            st.subheader("동일 프로젝트 업체별 견적 비교")
            proj_list = tasks["프로젝트명"].unique().tolist()
            sel_proj = st.selectbox("프로젝트 선택", proj_list)
            proj_tasks = tasks[tasks["프로젝트명"]==sel_proj].copy()
            vendors_in_proj = proj_tasks["업체명"].dropna().unique().tolist()

            # 대표 이미지 (첫 번째 이미지만)
            proj_row = projects[projects["project_name"]==sel_proj]
            if len(proj_row) > 0:
                raw_img = proj_row.iloc[0].get("sample_image")
                if raw_img:
                    try:
                        img_names = json.loads(raw_img) if str(raw_img).startswith("[") else [raw_img]
                    except Exception:
                        img_names = [raw_img]
                    if img_names:
                        try:
                            _, b64data = img_names[0].split(",", 1)
                            _rep_bytes = io.BytesIO(base64.b64decode(b64data))
                            ri_col, _ = st.columns([1, 3])
                            ri_col.image(_rep_bytes, caption="대표 이미지", use_container_width=True)
                        except Exception:
                            pass

            if len(vendors_in_proj)==0:
                st.info("이 프로젝트에 등록된 업체 견적이 없습니다.")
            else:
                # 업체별 총매입액 비교
                vtotal = proj_tasks.groupby("업체명")["총매입액"].sum().reset_index()
                vtotal.columns = ["업체명","총매입액"]
                fig = px.bar(vtotal, x="업체명", y="총매입액",
                             color="업체명", text="총매입액",
                             title=f"[{sel_proj}] 업체별 총 매입액 비교")
                fig.update_traces(texttemplate="%{text:,}원", textposition="outside")
                fig.update_layout(showlegend=False, height=350)
                st.plotly_chart(fig, use_container_width=True)

                # 과업별 단가 비교 (같은 과업명 기준)
                st.subheader("과업별 단가 비교")
                if proj_tasks["과업명"].nunique() > 0:
                    pivot = proj_tasks.pivot_table(
                        index="과업명", columns="업체명", values="단가", aggfunc="mean"
                    ).reset_index()
                    _pivot_fmt = {c: "{:,.0f}" for c in pivot.columns if c != "과업명"}
                    st.dataframe(pivot.style.format(_pivot_fmt), use_container_width=True, hide_index=True)

                    fig2 = px.bar(
                        proj_tasks.dropna(subset=["업체명"]),
                        x="과업명", y="단가", color="업체명",
                        barmode="group",
                        title=f"[{sel_proj}] 과업별 업체 단가 비교"
                    )
                    st.plotly_chart(fig2, use_container_width=True)

                # 업체별 상세 테이블
                st.subheader("업체별 견적 상세")
                for v in vendors_in_proj:
                    vdf = proj_tasks[proj_tasks["업체명"]==v].copy()
                    vtot = int(vdf["총매입액"].sum())
                    st.markdown(f"**{v}** — 총 매입액 {vtot:,}원")
                    _NUM_FMT = {"단가":"{:,.0f}", "총작업수량":"{:,.0f}", "총매입액":"{:,.0f}"}
                    st.dataframe(
                        vdf[["과업명","과업유형","단가","통화","단위","총작업수량","총매입액","기준일","비고"]].style.format(_NUM_FMT),
                        use_container_width=True, hide_index=True
                    )

        # ── Tab2: 업체별 평균 단가 ───────────
        with tab2:
            st.subheader("업체별 평균 단가 분석")
            st.caption("업체 × 과업유형 기준으로 평균 단가를 집계합니다.")

            cur_f = st.selectbox("통화 선택", sorted(tasks["통화"].dropna().unique()), key="cf_v")
            tf = tasks[tasks["통화"]==cur_f].dropna(subset=["업체명"])

            if len(tf)==0:
                st.info("선택한 통화의 데이터가 없습니다.")
            else:
                # 업체 × 과업유형 평균
                expanded = []
                for _, r in tf.iterrows():
                    for t in str(r["과업유형"]).split(","):
                        nr = r.copy(); nr["과업유형_단일"] = t.strip()
                        expanded.append(nr)
                exp_df = pd.DataFrame(expanded)

                avg_df = exp_df.groupby(["업체명","과업유형_단일"])["단가"].mean().reset_index()
                avg_df.columns = ["업체명","과업유형","평균단가"]

                fig = px.bar(avg_df, x="과업유형", y="평균단가", color="업체명",
                             barmode="group",
                             title=f"업체별 과업유형 평균 단가 ({cur_f})")
                st.plotly_chart(fig, use_container_width=True)

                # 피벗 테이블
                pivot2 = avg_df.pivot(index="업체명", columns="과업유형", values="평균단가").reset_index()
                st.dataframe(pivot2.style.format({c: "{:,.0f}" for c in pivot2.columns if c!="업체명"}),
                             use_container_width=True)

                # 업체별 총 매입액
                st.subheader("업체별 총 매입액 합계")
                vsum = tf.groupby("업체명")["총매입액"].sum().reset_index().sort_values("총매입액", ascending=False)
                vsum["총매입액"] = vsum["총매입액"].apply(lambda x: f"{int(x):,}원")
                st.dataframe(vsum, use_container_width=True, hide_index=True)

        # ── Tab3: 과업유형별 분석 ────────────
        with tab3:
            st.subheader("과업유형별 단가 분석")
            cur_f2 = st.selectbox("통화 선택", sorted(tasks["통화"].dropna().unique()), key="cf_t")
            tf2 = tasks[tasks["통화"]==cur_f2]

            summary = tf2.groupby("과업유형")["단가"].agg(
                건수="count", 평균단가="mean", 최소단가="min", 최대단가="max"
            ).reset_index()
            for col in ["평균단가","최소단가","최대단가"]:
                summary[col] = summary[col].map(lambda x: f"{int(x):,} {cur_f2}")
            st.dataframe(summary, use_container_width=True, hide_index=True)

            st.divider()
            st.subheader("분야 × 과업유형 분포")
            exp2 = []
            for _, r in tasks.iterrows():
                for t in str(r["과업유형"]).split(","):
                    nr = r.copy(); nr["과업유형_단일"] = t.strip()
                    exp2.append(nr)
            if exp2:
                exp2_df = pd.DataFrame(exp2)
                dc = exp2_df.groupby(["분야","과업유형_단일"]).size().reset_index(name="건수")
                fig3 = px.bar(dc, x="분야", y="건수", color="과업유형_단일",
                              barmode="group", title="분야별 과업유형 분포")
                st.plotly_chart(fig3, use_container_width=True)

        # ── Tab4: 원본 데이터 ────────────────
        with tab4:
            st.subheader("전체 원본 데이터")
            st.download_button(
                label="CSV 다운로드",
                data=tasks.to_csv(index=False, encoding="utf-8-sig"),
                file_name=f"project_tasks_{date.today()}.csv",
                mime="text/csv"
            )
            _NUM_FMT = {"단가":"{:,.0f}", "총작업수량":"{:,.0f}", "총매입액":"{:,.0f}"}
            st.dataframe(tasks.style.format(_NUM_FMT), use_container_width=True, hide_index=True)
