import os
import json
import sqlite3
import hashlib
import secrets
from datetime import date, datetime, time, timedelta
from io import BytesIO

import pandas as pd
import plotly.express as px
import streamlit as st

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "hcm_lite.db")
st.set_page_config(page_title="HCM Lite", page_icon="🏢", layout="wide")

# -------------------------- Database --------------------------
def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def q(sql, params=(), one=False, all_rows=False):
    with db() as conn:
        cur = conn.execute(sql, params)
        rows = cur.fetchall() if all_rows or one else None
        if one:
            return rows[0] if rows else None
        if all_rows:
            return [dict(r) for r in rows]
        conn.commit()
        return cur.lastrowid

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS company (
            id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL,
            office_start TEXT NOT NULL DEFAULT '09:00',
            grace_minutes INTEGER NOT NULL DEFAULT 15,
            weekend_days TEXT NOT NULL DEFAULT '5,6',
            office_lat REAL, office_lon REAL, office_radius_m INTEGER DEFAULT 150,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('Admin','HR','Employee')),
            active INTEGER NOT NULL DEFAULT 1, must_change_password INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS designations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT, employee_code TEXT UNIQUE NOT NULL,
            user_id INTEGER UNIQUE NOT NULL REFERENCES users(id), full_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL, phone TEXT DEFAULT '', address TEXT DEFAULT '',
            department_id INTEGER REFERENCES departments(id), designation_id INTEGER REFERENCES designations(id),
            join_date TEXT NOT NULL, employment_status TEXT NOT NULL DEFAULT 'Active',
            basic_salary REAL NOT NULL CHECK(basic_salary > 0), created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS leave_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL,
            annual_quota REAL NOT NULL DEFAULT 0, paid INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS holidays (
            id INTEGER PRIMARY KEY AUTOINCREMENT, holiday_date TEXT UNIQUE NOT NULL, name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id INTEGER NOT NULL REFERENCES employees(id),
            work_date TEXT NOT NULL, check_in TEXT, check_out TEXT, status TEXT NOT NULL,
            hours REAL DEFAULT 0, source TEXT NOT NULL DEFAULT 'web',
            UNIQUE(employee_id, work_date)
        );
        CREATE TABLE IF NOT EXISTS attendance_corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id INTEGER NOT NULL REFERENCES employees(id),
            work_date TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Pending',
            reviewed_by INTEGER REFERENCES users(id), reviewer_comment TEXT DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS leave_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id INTEGER NOT NULL REFERENCES employees(id),
            leave_type_id INTEGER NOT NULL REFERENCES leave_types(id), start_date TEXT NOT NULL,
            end_date TEXT NOT NULL, days REAL NOT NULL, reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending', reviewed_by INTEGER REFERENCES users(id),
            reviewer_comment TEXT DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payroll_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, month TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'Draft', created_by INTEGER REFERENCES users(id),
            finalized_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL, finalized_at TEXT
        );
        CREATE TABLE IF NOT EXISTS payslips (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL REFERENCES payroll_runs(id),
            employee_id INTEGER NOT NULL REFERENCES employees(id), basic REAL NOT NULL, hra REAL NOT NULL,
            medical REAL NOT NULL, gross REAL NOT NULL, absent_deduction REAL NOT NULL,
            unpaid_deduction REAL NOT NULL, halfday_deduction REAL NOT NULL, tax REAL NOT NULL,
            net REAL NOT NULL, UNIQUE(run_id, employee_id)
        );
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT NOT NULL,
            created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER REFERENCES users(id),
            message TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, actor_user_id INTEGER REFERENCES users(id),
            action TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT,
            before_json TEXT, after_json TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ai_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER REFERENCES users(id),
            question TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """)
        # Default leave types are configuration, not employee/demo data.
        for name, quota, paid in [("Annual",14,1),("Sick",8,1),("Casual",10,1),("Unpaid",0,0)]:
            c.execute("INSERT OR IGNORE INTO leave_types(name,annual_quota,paid) VALUES(?,?,?)", (name,quota,paid))
        c.commit()

def now():
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")

def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return f"{salt}${digest}"

def verify_password(password, stored):
    try:
        salt, digest = stored.split("$", 1)
        return secrets.compare_digest(hash_password(password, salt).split("$",1)[1], digest)
    except Exception:
        return False

def audit(actor, action, entity, entity_id=None, before=None, after=None):
    q("INSERT INTO audit_log(actor_user_id,action,entity_type,entity_id,before_json,after_json,created_at) VALUES(?,?,?,?,?,?,?)",
      (actor, action, entity, str(entity_id) if entity_id is not None else None,
       json.dumps(before, default=str) if before is not None else None,
       json.dumps(after, default=str) if after is not None else None, now()))

def notify(user_id, message):
    if user_id:
        q("INSERT INTO notifications(user_id,message,created_at) VALUES(?,?,?)", (user_id,message,now()))

def company():
    return q("SELECT * FROM company WHERE id=1", one=True)

def user_by_id(uid):
    return q("SELECT * FROM users WHERE id=?", (uid,), one=True)

def employee_for_user(uid):
    return q("SELECT * FROM employees WHERE user_id=?", (uid,), one=True)

def employees_df(where="", params=()):
    sql = """SELECT e.*, d.name department, g.name designation, u.active
             FROM employees e LEFT JOIN departments d ON d.id=e.department_id
             LEFT JOIN designations g ON g.id=e.designation_id
             JOIN users u ON u.id=e.user_id"""
    if where: sql += " WHERE " + where
    return pd.DataFrame(q(sql, params, all_rows=True))

def working_days(start, end):
    co = company()
    weekends = set(int(x) for x in (co["weekend_days"] if co else "5,6").split(",") if x.strip())
    holidays = {r["holiday_date"] for r in q("SELECT holiday_date FROM holidays WHERE holiday_date BETWEEN ? AND ?", (str(start),str(end)), all_rows=True)}
    d = start
    count = 0
    while d <= end:
        if d.weekday() not in weekends and d.isoformat() not in holidays:
            count += 1
        d += timedelta(days=1)
    return count

def leave_balance(employee_id, leave_type_id, year):
    lt = q("SELECT * FROM leave_types WHERE id=?", (leave_type_id,), one=True)
    if not lt: return 0
    if lt["name"].lower() == "unpaid": return float("inf")
    quota = float(lt["annual_quota"])
    rows = q("""SELECT start_date,end_date,days FROM leave_requests
                WHERE employee_id=? AND leave_type_id=? AND status='Approved'
                AND substr(start_date,1,4)=?""", (employee_id,leave_type_id,str(year)), all_rows=True)
    used = sum(float(r["days"]) for r in rows)
    return max(0, quota-used)

def calculate_tax(annual_gross):
    # Illustrative slabs from the challenge brief.
    x = annual_gross
    if x <= 600000: return 0
    if x <= 1200000: return (x-600000)*0.05
    if x <= 2400000: return 30000+(x-1200000)*0.15
    if x <= 3600000: return 210000+(x-2400000)*0.25
    return 510000+(x-3600000)*0.35

def flash(kind, message):
    getattr(st, kind)(message)

# -------------------------- First-run setup --------------------------
init_db()
co = company()
if not co:
    st.title("🏢 HCM Lite")
    st.subheader("First-run company setup")
    st.info("Create your company and the first Admin account. No employee demo data is preloaded.")
    with st.form("first_setup"):
        cname = st.text_input("Company name", placeholder="Example Company")
        admin_name = st.text_input("Admin full name")
        email = st.text_input("Admin email")
        pw = st.text_input("Admin password (minimum 10 characters)", type="password")
        confirm = st.text_input("Confirm password", type="password")
        start = st.time_input("Office start time", value=time(9,0))
        grace = st.number_input("Grace period (minutes)", min_value=0, max_value=120, value=15)
        submitted = st.form_submit_button("Create company and Admin", type="primary")
    if submitted:
        if not cname.strip() or not admin_name.strip() or "@" not in email or len(pw)<10 or pw!=confirm:
            st.error("Enter a company name, Admin name, valid email, matching passwords of at least 10 characters.")
        else:
            with db() as c:
                c.execute("INSERT INTO company(id,name,office_start,grace_minutes,created_at) VALUES(1,?,?,?,?)",
                          (cname.strip(),start.strftime("%H:%M"),int(grace),now()))
                cur = c.execute("INSERT INTO users(email,password_hash,role,created_at) VALUES(?,?,?,?)",
                                (email.strip().lower(),hash_password(pw),"Admin",now()))
                c.commit()
                uid = cur.lastrowid
            audit(uid,"CREATE","company",1,after={"name":cname})
            st.success("Setup complete. Sign in below.")
            st.rerun()
    st.stop()

# -------------------------- Login --------------------------
if "user_id" not in st.session_state:
    st.session_state.user_id = None

if not st.session_state.user_id:
    st.title("🏢 HCM Lite")
    st.caption("AI-assisted Human Capital Management")
    with st.form("login"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        ok = st.form_submit_button("Sign in", type="primary")
    if ok:
        u = q("SELECT * FROM users WHERE lower(email)=lower(?) AND active=1", (email.strip(),), one=True)
        if u and verify_password(password,u["password_hash"]):
            st.session_state.user_id = u["id"]
            st.rerun()
        else:
            st.error("Invalid email or password, or account deactivated.")
    st.caption("First launch? Create the Admin account using the setup form.")
    st.stop()

current = user_by_id(st.session_state.user_id)
if not current or not current["active"]:
    st.session_state.user_id = None
    st.rerun()
role = current["role"]
my_emp = employee_for_user(current["id"])
st.sidebar.title("🏢 HCM Lite")
st.sidebar.caption(f"{company()['name']} · {role}")
if st.sidebar.button("Log out"):
    st.session_state.user_id = None
    st.rerun()

if current["must_change_password"]:
    st.title("Change temporary password")
    with st.form("change_pw"):
        p1 = st.text_input("New password (minimum 10 characters)", type="password")
        p2 = st.text_input("Confirm new password", type="password")
        submit = st.form_submit_button("Update password", type="primary")
    if submit:
        if len(p1)<10 or p1 != p2:
            st.error("Passwords must match and be at least 10 characters.")
        else:
            q("UPDATE users SET password_hash=?,must_change_password=0 WHERE id=?", (hash_password(p1),current["id"]))
            audit(current["id"],"CHANGE_PASSWORD","user",current["id"])
            st.success("Password updated.")
            st.rerun()
    st.stop()

# -------------------------- Navigation --------------------------
menus = {
    "Admin": ["Dashboard","Organization Setup","Users & Employees","Attendance","Leave Management","Payroll","AI Assistant","Announcements","Audit Log"],
    "HR": ["Dashboard","Users & Employees","Attendance","Leave Management","Payroll","AI Assistant","Announcements"],
    "Employee": ["My Dashboard","My Attendance","My Leave","My Payslips","AI Assistant","Announcements"]
}
page = st.sidebar.radio("Navigation", menus[role])

st.title("🏢 HCM Lite")
st.caption(f"{company()['name']} · {page}")

# -------------------------- Admin setup --------------------------
if page == "Organization Setup" and role == "Admin":
    st.subheader("Company settings")
    co = company()
    with st.form("company_settings"):
        name = st.text_input("Company name", co["name"])
        office_start = st.time_input("Office start", value=datetime.strptime(co["office_start"],"%H:%M").time())
        grace = st.number_input("Grace minutes",0,120,int(co["grace_minutes"]))
        weekend = st.multiselect("Weekend days", options=[0,1,2,3,4,5,6], default=[int(x) for x in co["weekend_days"].split(",") if x],
                                 format_func=lambda x:["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"][x])
        lat = st.number_input("Office latitude (optional)", value=float(co["office_lat"] or 0), format="%.6f")
        lon = st.number_input("Office longitude (optional)", value=float(co["office_lon"] or 0), format="%.6f")
        radius = st.number_input("Office radius in metres",100,5000,int(co["office_radius_m"] or 150))
        save = st.form_submit_button("Save settings")
    if save:
        q("UPDATE company SET name=?,office_start=?,grace_minutes=?,weekend_days=?,office_lat=?,office_lon=?,office_radius_m=? WHERE id=1",
          (name.strip(),office_start.strftime("%H:%M"),int(grace),",".join(map(str,weekend)),lat if lat else None,lon if lon else None,int(radius)))
        audit(current["id"],"UPDATE","company",1,after={"name":name,"office_start":office_start.strftime("%H:%M"),"grace":grace})
        st.success("Settings saved.")
    c1,c2 = st.columns(2)
    with c1:
        st.markdown("**Departments**")
        with st.form("dept"):
            dn=st.text_input("New department")
            add=st.form_submit_button("Add department")
        if add and dn.strip():
            try:
                q("INSERT INTO departments(name) VALUES(?)",(dn.strip(),)); audit(current["id"],"CREATE","department",dn)
                st.success("Department added.")
            except sqlite3.IntegrityError: st.error("Department already exists.")
        st.dataframe(pd.DataFrame(q("SELECT * FROM departments",all_rows=True)),use_container_width=True,hide_index=True)
    with c2:
        st.markdown("**Designations**")
        with st.form("designation"):
            gn=st.text_input("New designation")
            addg=st.form_submit_button("Add designation")
        if addg and gn.strip():
            try:
                q("INSERT INTO designations(name) VALUES(?)",(gn.strip(),)); audit(current["id"],"CREATE","designation",gn)
                st.success("Designation added.")
            except sqlite3.IntegrityError: st.error("Designation already exists.")
        st.dataframe(pd.DataFrame(q("SELECT * FROM designations",all_rows=True)),use_container_width=True,hide_index=True)
    st.markdown("**Leave types and quotas**")
    with st.form("leave_type"):
        lname=st.text_input("Leave type name")
        quota=st.number_input("Annual quota (days)",0.0,365.0,10.0)
        paid=st.checkbox("Paid leave",value=True)
        addlt=st.form_submit_button("Add leave type")
    if addlt and lname.strip():
        try:
            q("INSERT INTO leave_types(name,annual_quota,paid) VALUES(?,?,?)",(lname.strip(),quota,int(paid)))
            audit(current["id"],"CREATE","leave_type",lname); st.success("Leave type added.")
        except sqlite3.IntegrityError: st.error("That leave type already exists.")
    st.dataframe(pd.DataFrame(q("SELECT * FROM leave_types",all_rows=True)),use_container_width=True,hide_index=True)
    st.markdown("**Public holidays**")
    with st.form("holiday"):
        hd=st.date_input("Holiday date")
        hn=st.text_input("Holiday name")
        addh=st.form_submit_button("Add holiday")
    if addh and hn.strip():
        try:
            q("INSERT INTO holidays(holiday_date,name) VALUES(?,?)",(hd.isoformat(),hn.strip()))
            audit(current["id"],"CREATE","holiday",hd.isoformat()); st.success("Holiday added.")
        except sqlite3.IntegrityError: st.error("A holiday already exists on that date.")
    st.dataframe(pd.DataFrame(q("SELECT * FROM holidays ORDER BY holiday_date",all_rows=True)),use_container_width=True,hide_index=True)

# -------------------------- User and employee management --------------------------
elif page == "Users & Employees" and role in ("Admin","HR"):
    st.subheader("Employee directory")
    if role == "HR":
        st.caption("HR can register and manage employee details; only Admin can create HR accounts.")
    with st.expander("Create HR account (Admin only)",expanded=False):
        if role != "Admin":
            st.caption("Only Admin can create HR accounts.")
        else:
            with st.form("create_hr"):
                hr_name=st.text_input("HR full name")
                hr_email=st.text_input("HR email")
                hr_pw=st.text_input("Temporary password (minimum 10 characters)",type="password")
                create_hr=st.form_submit_button("Create HR login")
            if create_hr:
                if not hr_name.strip() or "@" not in hr_email or len(hr_pw)<10:
                    st.error("Enter a name, valid email and password of at least 10 characters.")
                else:
                    try:
                        uid=q("INSERT INTO users(email,password_hash,role,must_change_password,created_at) VALUES(?,?,?,?,?)",
                              (hr_email.strip().lower(),hash_password(hr_pw),"HR",1,now()))
                        audit(current["id"],"CREATE","user",uid,after={"email":hr_email,"role":"HR"})
                        st.success("HR account created. Share the temporary password securely.")
                    except sqlite3.IntegrityError: st.error("Email already exists.")
    deps=q("SELECT * FROM departments ORDER BY name",all_rows=True)
    desigs=q("SELECT * FROM designations ORDER BY name",all_rows=True)
    with st.expander("Register new employee",expanded=employees_df().empty):
        with st.form("new_employee"):
            code=st.text_input("Employee code")
            fullname=st.text_input("Full name")
            email=st.text_input("Employee email")
            phone=st.text_input("Phone")
            address=st.text_input("Address")
            dept=st.selectbox("Department",deps,format_func=lambda x:x["name"]) if deps else None
            desig=st.selectbox("Designation",desigs,format_func=lambda x:x["name"]) if desigs else None
            join=st.date_input("Joining date",value=date.today())
            salary=st.number_input("Basic monthly salary",min_value=1.0,value=50000.0,step=1000.0)
            temp=st.text_input("Temporary password (minimum 10 characters)",type="password")
            create=st.form_submit_button("Register employee",type="primary")
        if create:
            if not all([code.strip(),fullname.strip(),"@" in email,len(temp)>=10,dept,desig]):
                st.error("Complete required fields, add a department and designation, and use a password of at least 10 characters.")
            else:
                try:
                    with db() as c:
                        cur=c.execute("INSERT INTO users(email,password_hash,role,must_change_password,created_at) VALUES(?,?,?,?,?)",
                                      (email.strip().lower(),hash_password(temp),"Employee",1,now()))
                        uid=cur.lastrowid
                        cur=c.execute("""INSERT INTO employees(employee_code,user_id,full_name,email,phone,address,department_id,designation_id,join_date,basic_salary,created_at)
                                         VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                                      (code.strip(),uid,fullname.strip(),email.strip().lower(),phone,address,dept["id"],desig["id"],join.isoformat(),float(salary),now()))
                        eid=cur.lastrowid
                        c.commit()
                    audit(current["id"],"CREATE","employee",eid,after={"employee_code":code,"full_name":fullname,"email":email})
                    notify(uid,"Your employee account has been created. Sign in and change your temporary password.")
                    st.success("Employee registered. A login was created; provide the temporary password securely.")
                    st.rerun()
                except sqlite3.IntegrityError as e:
                    st.error("Employee code or email already exists.")
    df=employees_df()
    if not df.empty:
        search=st.text_input("Search by name, email or employee code")
        if search:
            df=df[df.astype(str).apply(lambda col: col.str.contains(search,case=False,na=False)).any(axis=1)]
        st.dataframe(df[["employee_code","full_name","email","department","designation","join_date","employment_status","basic_salary","active"]],use_container_width=True,hide_index=True)
        csv=df.to_csv(index=False).encode("utf-8")
        st.download_button("Export employee directory CSV",csv,"employees.csv","text/csv")
        if role=="Admin":
            st.markdown("**Account management**")
            allusers=q("SELECT id,email,role,active FROM users ORDER BY role,email",all_rows=True)
            usersdf=pd.DataFrame(allusers)
            st.dataframe(usersdf,use_container_width=True,hide_index=True)
            with st.form("deactivate_user"):
                target=st.selectbox("Account to activate/deactivate",allusers,format_func=lambda x:f"{x['email']} ({x['role']})") if allusers else None
                active=st.selectbox("Set status",["Active","Deactivated"])
                change=st.form_submit_button("Update account status")
            if change and target:
                if target["id"]==current["id"] and active=="Deactivated":
                    st.error("You cannot deactivate your own active session.")
                else:
                    q("UPDATE users SET active=? WHERE id=?",(1 if active=="Active" else 0,target["id"]))
                    audit(current["id"],"UPDATE","user",target["id"],after={"active":active})
                    st.success("Account status updated.")
    else:
        st.info("No employees registered yet. Register the first employee above.")

# -------------------------- Attendance --------------------------
elif page in ("Attendance","My Attendance"):
    if role == "Employee":
        if not my_emp:
            st.warning("Your employee profile is not linked. Ask HR to check your account.")
            st.stop()
        people=[dict(my_emp)]
    else:
        df=employees_df()
        people=df.to_dict("records")
    st.subheader("Attendance")
    if role=="Employee":
        st.write(f"Welcome, **{my_emp['full_name']}**")
        att=q("SELECT * FROM attendance WHERE employee_id=? AND work_date=?",(my_emp["id"],date.today().isoformat()),one=True)
        c1,c2=st.columns(2)
        with c1:
            if not att:
                if st.button("Check in",type="primary"):
                    co=company()
                    current_time=datetime.now()
                    cutoff=datetime.strptime(co["office_start"],"%H:%M")+timedelta(minutes=int(co["grace_minutes"]))
                    status="Late" if current_time.time()>cutoff.time() else "Present"
                    try:
                        q("INSERT INTO attendance(employee_id,work_date,check_in,status,source) VALUES(?,?,?,?,?)",
                          (my_emp["id"],date.today().isoformat(),now(),status,"web"))
                        audit(current["id"],"CHECK_IN","attendance",my_emp["id"],after={"date":date.today().isoformat(),"status":status})
                        st.success(f"Checked in. Status: {status}")
                        st.rerun()
                    except sqlite3.IntegrityError: st.error("Attendance already exists for today.")
            else:
                st.metric("Today's status",att["status"])
                st.caption(f"Check-in: {att['check_in'] or '—'}")
        with c2:
            if att and not att["check_out"]:
                if st.button("Check out"):
                    ci=datetime.fromisoformat(att["check_in"])
                    hours=max(0,(datetime.now()-ci).total_seconds()/3600)
                    status=att["status"]
                    if hours<5: status="Half Day"
                    q("UPDATE attendance SET check_out=?,hours=?,status=? WHERE id=?",(now(),round(hours,2),status,att["id"]))
                    audit(current["id"],"CHECK_OUT","attendance",att["id"],after={"hours":round(hours,2),"status":status})
                    st.success(f"Checked out. Hours worked: {hours:.2f}")
                    st.rerun()
        st.markdown("**Request attendance correction**")
        with st.form("correction"):
            cd=st.date_input("Date to correct",value=date.today())
            reason=st.text_area("Reason")
            submit=st.form_submit_button("Submit correction request")
        if submit and reason.strip():
            q("INSERT INTO attendance_corrections(employee_id,work_date,reason,created_at) VALUES(?,?,?,?)",(my_emp["id"],cd.isoformat(),reason.strip(),now()))
            audit(current["id"],"CREATE","attendance_correction",my_emp["id"])
            st.success("Correction request submitted.")
        hist=pd.DataFrame(q("SELECT work_date,check_in,check_out,status,hours FROM attendance WHERE employee_id=? ORDER BY work_date DESC",(my_emp["id"],),all_rows=True))
        st.dataframe(hist,use_container_width=True,hide_index=True)
    else:
        today=date.today().isoformat()
        st.markdown(f"**Daily attendance board — {today}**")
        if not df.empty:
            a=pd.DataFrame(q("""SELECT e.employee_code,e.full_name,d.name department,a.work_date,a.check_in,a.check_out,a.status,a.hours
                               FROM employees e LEFT JOIN departments d ON d.id=e.department_id
                               LEFT JOIN attendance a ON a.employee_id=e.id AND a.work_date=?""",(today,),all_rows=True))
            if not a.empty:
                a["status"]=a["status"].fillna("Absent")
                st.dataframe(a,use_container_width=True,hide_index=True)
                st.download_button("Export attendance CSV",a.to_csv(index=False).encode(),"attendance.csv","text/csv")
        corrections=q("""SELECT c.*,e.full_name,e.employee_code FROM attendance_corrections c JOIN employees e ON e.id=c.employee_id
                         WHERE c.status='Pending' ORDER BY c.created_at""",all_rows=True)
        if corrections and role in ("Admin","HR"):
            st.markdown("**Pending correction requests**")
            for cr in corrections:
                with st.expander(f"{cr['full_name']} — {cr['work_date']}"):
                    st.write(cr["reason"])
                    comment=st.text_input("Review comment",key=f"cc{cr['id']}")
                    decision=st.selectbox("Decision",["Approve","Reject"],key=f"cd{cr['id']}")
                    if st.button("Submit decision",key=f"cs{cr['id']}"):
                        q("UPDATE attendance_corrections SET status=?,reviewed_by=?,reviewer_comment=? WHERE id=?",
                          (decision+"d" if decision=="Approve" else "Rejected",current["id"],comment,cr["id"]))
                        notify(q("SELECT user_id FROM employees WHERE id=?",(cr["employee_id"],),one=True)["user_id"],f"Your attendance correction for {cr['work_date']} was {decision.lower()}.")
                        audit(current["id"],"REVIEW","attendance_correction",cr["id"],after={"decision":decision,"comment":comment})
                        st.rerun()

# -------------------------- Leave --------------------------
elif page in ("Leave Management","My Leave"):
    st.subheader("Leave management")
    if role=="Employee":
        if not my_emp:
            st.warning("Your employee profile is not linked."); st.stop()
        leave_types=q("SELECT * FROM leave_types ORDER BY name",all_rows=True)
        with st.form("apply_leave"):
            lt=st.selectbox("Leave type",leave_types,format_func=lambda x:x["name"])
            start=st.date_input("Start date",value=date.today()+timedelta(days=1))
            end=st.date_input("End date",value=date.today()+timedelta(days=1))
            reason=st.text_area("Reason")
            smart_text=st.text_input("Optional: describe your leave in plain words (AI prefill can be added via AI Assistant)")
            submit=st.form_submit_button("Submit leave request",type="primary")
        if submit:
            if end<start or not reason.strip():
                st.error("End date must be on or after start date, and a reason is required.")
            else:
                days=working_days(start,end)
                overlap=q("""SELECT id FROM leave_requests WHERE employee_id=? AND status IN ('Pending','Approved')
                             AND NOT(end_date<? OR start_date>?)""",(my_emp["id"],start.isoformat(),end.isoformat()),one=True)
                balance=leave_balance(my_emp["id"],lt["id"],start.year)
                if days<=0: st.error("The selected range contains no working days.")
                elif overlap: st.error("This request overlaps an existing pending or approved request.")
                elif lt["name"].lower()!="unpaid" and days>balance: st.error(f"Insufficient balance. Available: {balance:g} day(s).")
                else:
                    rid=q("INSERT INTO leave_requests(employee_id,leave_type_id,start_date,end_date,days,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                          (my_emp["id"],lt["id"],start.isoformat(),end.isoformat(),days,reason.strip(),now()))
                    audit(current["id"],"CREATE","leave_request",rid,after={"start":start.isoformat(),"end":end.isoformat(),"days":days})
                    st.success(f"Leave request submitted for {days} working day(s).")
                    st.rerun()
        st.markdown("**My leave balance**")
        for lt in leave_types:
            if lt["name"].lower()=="unpaid": continue
            st.metric(f"{lt['name']} leave remaining",f"{leave_balance(my_emp['id'],lt['id'],date.today().year):g} days")
        records=q("""SELECT l.*,t.name leave_type FROM leave_requests l JOIN leave_types t ON t.id=l.leave_type_id
                     WHERE l.employee_id=? ORDER BY l.created_at DESC""",(my_emp["id"],),all_rows=True)
        st.dataframe(pd.DataFrame(records),use_container_width=True,hide_index=True)
        # Cancel own pending requests only.
        pending=[r for r in records if r["status"]=="Pending"]
        if pending:
            with st.form("cancel_leave"):
                cancel=st.selectbox("Cancel pending request",pending,format_func=lambda x:f"{x['leave_type']} · {x['start_date']} to {x['end_date']}")
                cancel_btn=st.form_submit_button("Cancel request")
            if cancel_btn and cancel:
                q("UPDATE leave_requests SET status='Cancelled' WHERE id=? AND employee_id=? AND status='Pending'",(cancel["id"],my_emp["id"]))
                audit(current["id"],"CANCEL","leave_request",cancel["id"])
                st.success("Request cancelled."); st.rerun()
    else:
        records=q("""SELECT l.*,e.full_name,e.employee_code,t.name leave_type FROM leave_requests l
                     JOIN employees e ON e.id=l.employee_id JOIN leave_types t ON t.id=l.leave_type_id
                     ORDER BY CASE l.status WHEN 'Pending' THEN 0 ELSE 1 END,l.created_at DESC""",all_rows=True)
        if records:
            st.dataframe(pd.DataFrame(records),use_container_width=True,hide_index=True)
            pending=[r for r in records if r["status"]=="Pending"]
            if pending:
                st.markdown("**Review pending leave requests**")
                with st.form("review_leave"):
                    req=st.selectbox("Request",pending,format_func=lambda x:f"{x['full_name']} · {x['leave_type']} · {x['start_date']} to {x['end_date']}")
                    decision=st.selectbox("Decision",["Approve","Reject"])
                    comment=st.text_input("Comment")
                    review=st.form_submit_button("Save decision",type="primary")
                if review and req:
                    q("UPDATE leave_requests SET status=?,reviewed_by=?,reviewer_comment=? WHERE id=? AND status='Pending'",
                      ("Approved" if decision=="Approve" else "Rejected",current["id"],comment,req["id"]))
                    emp=q("SELECT user_id FROM employees WHERE id=?",(req["employee_id"],),one=True)
                    notify(emp["user_id"],f"Your {req['leave_type']} leave request ({req['start_date']} to {req['end_date']}) was {decision.lower()}.")
                    audit(current["id"],"REVIEW","leave_request",req["id"],after={"decision":decision,"comment":comment})
                    st.success(f"Request {decision.lower()}d."); st.rerun()
        else:
            st.info("No leave requests yet.")

# -------------------------- Payroll --------------------------
elif page in ("Payroll","My Payslips"):
    st.subheader("Payroll")
    if role=="Employee":
        if not my_emp:
            st.warning("Your employee profile is not linked."); st.stop()
        slips=q("""SELECT p.*,r.month,r.status run_status FROM payslips p JOIN payroll_runs r ON r.id=p.run_id
                   WHERE p.employee_id=? AND r.status='Finalized' ORDER BY r.month DESC""",(my_emp["id"],),all_rows=True)
        if slips:
            st.dataframe(pd.DataFrame(slips),use_container_width=True,hide_index=True)
            selected=st.selectbox("Payslip month",slips,format_func=lambda x:x["month"])
            if selected:
                from reportlab.lib.pagesizes import A4
                from reportlab.pdfgen import canvas
                buff=BytesIO(); cv=canvas.Canvas(buff,pagesize=A4)
                cv.setFont("Helvetica-Bold",18); cv.drawString(50,790,f"{company()['name']} - Payslip")
                cv.setFont("Helvetica",11)
                lines=[f"Employee: {my_emp['full_name']}",f"Employee code: {my_emp['employee_code']}",f"Month: {selected['month']}",
                       f"Basic salary: {selected['basic']:.2f}",f"HRA: {selected['hra']:.2f}",f"Medical: {selected['medical']:.2f}",
                       f"Gross: {selected['gross']:.2f}",f"Absence deduction: {selected['absent_deduction']:.2f}",
                       f"Unpaid leave deduction: {selected['unpaid_deduction']:.2f}",f"Half-day deduction: {selected['halfday_deduction']:.2f}",
                       f"Tax: {selected['tax']:.2f}",f"Net pay: {selected['net']:.2f}"]
                y=750
                for line in lines: cv.drawString(50,y,line); y-=28
                cv.save()
                st.download_button("Download payslip PDF",buff.getvalue(),f"payslip_{selected['month']}.pdf","application/pdf")
        else: st.info("No finalized payslips available.")
    else:
        emps=employees_df()
        month=st.text_input("Payroll month (YYYY-MM)",value=date.today().strftime("%Y-%m"))
        if not (len(month)==7 and month[4]=="-" and month[:4].isdigit() and month[5:].isdigit() and 1<=int(month[5:])<=12):
            st.error("Enter month as YYYY-MM.")
        else:
            run=q("SELECT * FROM payroll_runs WHERE month=?",(month,),one=True)
            if run and run["status"]=="Finalized":
                st.success("This payroll month is finalized and locked.")
            elif st.button("Create / refresh monthly payroll draft",type="primary"):
                # Rebuild draft for the selected month.
                with db() as c:
                    if run: c.execute("DELETE FROM payslips WHERE run_id=?",(run["id"],))
                    else:
                        cur=c.execute("INSERT INTO payroll_runs(month,status,created_by,created_at) VALUES(?,?,?,?)",(month,"Draft",current["id"],now()))
                        run_id=cur.lastrowid
                    if run: run_id=run["id"]
                    year,mon=map(int,month.split("-"))
                    first=date(year,mon,1)
                    last=date(year,mon, __import__("calendar").monthrange(year,mon)[1])
                    workdays=working_days(first,last) or 1
                    active=q("SELECT * FROM employees WHERE employment_status='Active'",all_rows=True)
                    for e in active:
                        basic=float(e["basic_salary"]); hra=basic*.40; medical=basic*.10; gross=basic+hra+medical
                        att=q("SELECT status FROM attendance WHERE employee_id=? AND work_date BETWEEN ? AND ?",(e["id"],first.isoformat(),last.isoformat()),all_rows=True)
                        leave=q("""SELECT l.days,t.name,t.paid FROM leave_requests l JOIN leave_types t ON t.id=l.leave_type_id
                                   WHERE l.employee_id=? AND l.status='Approved' AND l.start_date<=? AND l.end_date>=?""",
                                 (e["id"],last.isoformat(),first.isoformat()),all_rows=True)
                        absent=sum(1 for a in att if a["status"]=="Absent")
                        half=sum(1 for a in att if a["status"]=="Half Day")
                        unpaid=sum(float(l["days"]) for l in leave if l["name"].lower()=="unpaid")
                        per_day=gross/workdays
                        absent_d=absent*per_day; half_d=.5*half*per_day; unpaid_d=unpaid*per_day
                        tax=calculate_tax(gross*12)/12
                        net=max(0,gross-absent_d-half_d-unpaid_d-tax)
                        c.execute("""INSERT INTO payslips(run_id,employee_id,basic,hra,medical,gross,absent_deduction,unpaid_deduction,halfday_deduction,tax,net)
                                     VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(run_id,e["id"],basic,hra,medical,gross,absent_d,unpaid_d,half_d,tax,net))
                    c.commit()
                audit(current["id"],"CREATE","payroll_run",run_id,after={"month":month,"status":"Draft"})
                st.success("Payroll draft generated from current employee, attendance and approved leave records.")
                st.rerun()
            if run:
                slips=q("""SELECT p.*,e.full_name,e.employee_code FROM payslips p JOIN employees e ON e.id=p.employee_id
                           WHERE p.run_id=? ORDER BY e.full_name""",(run["id"],),all_rows=True)
                if slips:
                    st.dataframe(pd.DataFrame(slips),use_container_width=True,hide_index=True)
                    st.metric("Total net payroll",f"{sum(x['net'] for x in slips):,.2f}")
                if role=="Admin" and run["status"]=="Draft" and st.button("Finalize and lock payroll"):
                    q("UPDATE payroll_runs SET status='Finalized',finalized_by=?,finalized_at=? WHERE id=? AND status='Draft'",(current["id"],now(),run["id"]))
                    audit(current["id"],"FINALIZE","payroll_run",run["id"],after={"month":month,"status":"Finalized"})
                    st.success("Payroll finalized and locked."); st.rerun()
        runs=q("SELECT * FROM payroll_runs ORDER BY month DESC",all_rows=True)
        if runs: st.markdown("**Payroll runs**"); st.dataframe(pd.DataFrame(runs),use_container_width=True,hide_index=True)

# -------------------------- Dashboard --------------------------
elif page in ("Dashboard","My Dashboard"):
    if role=="Employee":
        if not my_emp:
            st.warning("Your employee profile is not linked."); st.stop()
        att=q("SELECT * FROM attendance WHERE employee_id=? AND work_date>=?",(my_emp["id"],date.today().replace(day=1).isoformat()),all_rows=True)
        leaves=q("SELECT * FROM leave_requests WHERE employee_id=? AND status='Pending'",(my_emp["id"],),all_rows=True)
        slips=q("""SELECT p.*,r.month FROM payslips p JOIN payroll_runs r ON r.id=p.run_id
                   WHERE p.employee_id=? AND r.status='Finalized' ORDER BY r.month DESC""",(my_emp["id"],),all_rows=True)
        c1,c2,c3,c4=st.columns(4)
        c1.metric("Attendance records this month",len(att))
        c2.metric("Late arrivals this month",sum(1 for x in att if x["status"]=="Late"))
        c3.metric("Pending leave requests",len(leaves))
        c4.metric("Latest net payslip",f"{slips[0]['net']:,.0f}" if slips else "—")
        st.markdown("**My recent attendance**")
        st.dataframe(pd.DataFrame(att).sort_values("work_date",ascending=False) if att else pd.DataFrame(),use_container_width=True,hide_index=True)
    else:
        emps=employees_df()
        today=date.today().isoformat()
        att=q("SELECT * FROM attendance WHERE work_date=?",(today,),all_rows=True)
        leave_today=q("""SELECT * FROM leave_requests WHERE status='Approved' AND start_date<=? AND end_date>=?""",(today,today),all_rows=True)
        pending=q("SELECT COUNT(*) n FROM leave_requests WHERE status='Pending'",one=True)["n"]
        present=sum(1 for x in att if x["status"] in ("Present","Late","Half Day","Missing Checkout"))
        late=sum(1 for x in att if x["status"]=="Late")
        c1,c2,c3,c4=st.columns(4)
        c1.metric("Active employees",len(emps[emps["employment_status"]=="Active"]) if not emps.empty else 0)
        c2.metric("Present / checked in today",present)
        c3.metric("Approved leave today",len(leave_today))
        c4.metric("Late today",late)
        st.metric("Pending leave approvals",pending)
        if not emps.empty:
            st.markdown("**Employees by department**")
            bydept=emps.groupby("department",dropna=False).size().reset_index(name="Employees")
            st.plotly_chart(px.bar(bydept,x="department",y="Employees",title="Headcount by department"),use_container_width=True)
            start_month=date.today().replace(day=1).isoformat()
            month_att=pd.DataFrame(q("SELECT work_date,status FROM attendance WHERE work_date>=?",(start_month,),all_rows=True))
            if not month_att.empty:
                trend=month_att.groupby(["work_date","status"]).size().reset_index(name="Count")
                st.plotly_chart(px.line(trend,x="work_date",y="Count",color="status",title="Attendance status trend"),use_container_width=True)
        st.markdown("**Recent announcements**")
        ann=q("SELECT a.*,u.email creator FROM announcements a LEFT JOIN users u ON u.id=a.created_by ORDER BY a.created_at DESC LIMIT 5",all_rows=True)
        for a in ann:
            with st.container(border=True):
                st.markdown(f"**{a['title']}**")
                st.write(a["body"])
                st.caption(a["created_at"])

# -------------------------- AI Assistant --------------------------
elif page == "AI Assistant":
    st.subheader("AI assistant")
    st.caption("AI-generated answers are based on permitted app data and policy rules. Verify important decisions.")
    st.info("Set AI_API_KEY and AI_BASE_URL in your environment to enable the hosted LLM. Without a key, the built-in safe analytics assistant still works.")
    question=st.text_input("Ask a question",placeholder="How many employees are late today?")
    if st.button("Ask assistant",type="primary") and question.strip():
        q("INSERT INTO ai_logs(user_id,question,created_at) VALUES(?,?,?)",(current["id"],question.strip(),now()))
        low=question.lower()
        if role=="Employee":
            if not my_emp:
                answer="Your employee profile is not linked. Please contact HR."
            elif any(x in low for x in ["salary","payroll","payslip"]):
                slip=q("""SELECT p.*,r.month FROM payslips p JOIN payroll_runs r ON r.id=p.run_id
                          WHERE p.employee_id=? AND r.status='Finalized' ORDER BY r.month DESC LIMIT 1""",(my_emp["id"],),one=True)
                answer=f"Your latest finalized net pay is {slip['net']:,.2f} for {slip['month']}." if slip else "You do not have a finalized payslip yet."
            elif "leave" in low and ("balance" in low or "left" in low or "remaining" in low):
                lts=q("SELECT * FROM leave_types",all_rows=True)
                answer="\n".join(f"- {lt['name']}: {leave_balance(my_emp['id'],lt['id'],date.today().year):g} days remaining" for lt in lts if lt["name"].lower()!="unpaid")
            elif "attendance" in low or "late" in low:
                rec=q("SELECT status,COUNT(*) n FROM attendance WHERE employee_id=? AND work_date>=? GROUP BY status",(my_emp["id"],date.today().replace(day=1).isoformat()),all_rows=True)
                answer="Your attendance this month:\n"+"\n".join(f"- {x['status']}: {x['n']}" for x in rec) if rec else "No attendance records this month."
            elif any(x in low for x in ["colleague","other employee","someone else","another employee"]):
                answer="I can't disclose another employee's private information, including their salary."
            else:
                answer="I can help with your own attendance, leave balance, finalized payslips and company policies. Try asking about one of those."
        else:
            if "headcount" in low or "employees" in low:
                answer=f"There are {len(employees_df())} registered employees."
            elif "late" in low:
                n=q("SELECT COUNT(*) n FROM attendance WHERE work_date=? AND status='Late'",(date.today().isoformat(),),one=True)["n"]
                answer=f"{n} employee(s) are marked late today."
            elif "leave" in low:
                n=q("SELECT COUNT(*) n FROM leave_requests WHERE status='Approved' AND start_date<=? AND end_date>=?",(date.today().isoformat(),date.today().isoformat()),one=True)["n"]
                p=q("SELECT COUNT(*) n FROM leave_requests WHERE status='Pending'",one=True)["n"]
                answer=f"{n} employee(s) have approved leave today; {p} request(s) are pending approval."
            elif "attendance" in low or "present" in low:
                records=q("SELECT status,COUNT(*) n FROM attendance WHERE work_date=? GROUP BY status",(date.today().isoformat(),),all_rows=True)
                answer=f"Attendance summary for {date.today()}:\n"+"\n".join(f"- {r['status']}: {r['n']}" for r in records) if records else "No attendance records have been recorded today."
            elif "policy" in low or "grace" in low or "late" in low:
                co=company()
                answer=f"Office starts at {co['office_start']}; grace period is {co['grace_minutes']} minutes. Employees arriving after the grace period are marked late. Leave quotas are configured under Organization Setup."
            else:
                answer="Try asking: 'How many employees are registered?', 'Who is late today?', 'How many employees are on leave today?' or 'Summarize attendance'."
        api_key=os.getenv("AI_API_KEY")
        base_url=os.getenv("AI_BASE_URL","https://api.openai.com/v1")
        model=os.getenv("AI_MODEL","gpt-4o-mini")
        # Optional OpenAI-compatible API. Only aggregate data/policy is sent; API key remains server-side.
        if api_key:
            try:
                import requests
                if role=="Employee":
                    context=f"Employee's own allowed context only. Role=Employee. Never disclose others' data. Built-in safe result: {answer}"
                else:
                    context=f"Role={role}. Current company aggregate/policy context. Built-in safe result: {answer}"
                resp=requests.post(base_url.rstrip("/")+"/chat/completions",
                    headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"},
                    json={"model":model,"messages":[
                        {"role":"system","content":"You are HCM Lite HR assistant. Never invent facts. Respect the user's role and data scope. Do not disclose other employees' private information to an employee. Use the supplied application result as the source of truth. If insufficient, say you do not know. Clearly label responses AI-generated."},
                        {"role":"user","content":f"Question: {question}\nApplication result/context: {context}"}],
                     "temperature":0.2},
                    timeout=15)
                resp.raise_for_status()
                answer=resp.json()["choices"][0]["message"]["content"]
            except Exception:
                answer += "\n\n(Hosted AI service unavailable; showing the built-in safe answer.)"
        st.markdown("**AI-generated answer**")
        st.write(answer)
        st.caption("For leave requests, this starter version gives guidance; confirm all dates and balances in the leave form before submission.")

# -------------------------- Announcements --------------------------
elif page == "Announcements":
    st.subheader("Announcements")
    if role in ("Admin","HR"):
        with st.form("announcement"):
            title=st.text_input("Title")
            body=st.text_area("Announcement")
            post=st.form_submit_button("Post announcement",type="primary")
        if post:
            if title.strip() and body.strip():
                aid=q("INSERT INTO announcements(title,body,created_by,created_at) VALUES(?,?,?,?)",(title.strip(),body.strip(),current["id"],now()))
                audit(current["id"],"CREATE","announcement",aid)
                # Notify all active users.
                for u in q("SELECT id FROM users WHERE active=1",all_rows=True):
                    notify(u["id"],f"New announcement: {title.strip()}")
                st.success("Announcement posted."); st.rerun()
            else: st.error("Title and announcement are required.")
    anns=q("SELECT a.*,u.email creator FROM announcements a LEFT JOIN users u ON u.id=a.created_by ORDER BY a.created_at DESC",all_rows=True)
    for a in anns:
        with st.container(border=True):
            st.markdown(f"**{a['title']}**")
            st.write(a["body"])
            st.caption(f"Posted by {a['creator'] or 'Admin'} · {a['created_at']}")
    st.markdown("**My notifications**")
    notes=q("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 20",(current["id"],),all_rows=True)
    for n in notes:
        st.write(("🔔 " if not n["is_read"] else "✓ ")+n["message"])
    if notes and st.button("Mark notifications as read"):
        q("UPDATE notifications SET is_read=1 WHERE user_id=?",(current["id"],)); st.rerun()

# -------------------------- Audit log --------------------------
elif page == "Audit Log" and role == "Admin":
    st.subheader("Audit log")
    rows=q("""SELECT a.id,u.email actor,a.action,a.entity_type,a.entity_id,a.before_json,a.after_json,a.created_at
              FROM audit_log a LEFT JOIN users u ON u.id=a.actor_user_id ORDER BY a.created_at DESC LIMIT 500""",all_rows=True)
    st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
    if rows: st.download_button("Download audit log CSV",pd.DataFrame(rows).to_csv(index=False).encode(),"audit_log.csv","text/csv")

st.sidebar.divider()
st.sidebar.caption("HCM Lite · Hackathon starter implementation")
