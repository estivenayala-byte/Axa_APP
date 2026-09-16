import os
import io
import pandas as pd
from datetime import datetime
from typing import Optional, List, Dict
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
import uvicorn
from pydantic import BaseModel
from enum import Enum

from sqlalchemy import create_engine, Column, Integer, String, Float, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# =============================================================
# PERSISTENCIA EN BASE DE DATOS (SUPABASE / POSTGRESQL / SQLITE)
# =============================================================

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    DATABASE_URL = "sqlite:///./pcl_database.db"

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL, 
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class CaseModel(Base):
    __tablename__ = "casos_pcl"

    id = Column(Integer, primary_key=True, index=True)
    document_type = Column(String(50), nullable=False)
    patient_id = Column(String(20), nullable=False)
    patient_name = Column(String(150), nullable=False)
    claim_number = Column(String(30), nullable=True, default="PENDIENTE")
    origin_type = Column(String(20), nullable=True, default="Laboral")
    event_type = Column(String(10), nullable=True, default="AT")
    qualification_type = Column(String(20), nullable=False)
    it_days = Column(Integer, nullable=True, default=0)
    company_name = Column(String(150), nullable=True, default="NO ESPECIFICADO")
    company_id = Column(String(30), nullable=True)
    axa_filing_date = Column(String(20), nullable=False)
    insurer = Column(String(100), default="AXA Colpatria Seguros")
    module_state = Column(String(50), nullable=False)
    sub_step = Column(String(50), nullable=False)
    assigned_to = Column(String(100), nullable=False)
    assigned_role = Column(String(50), nullable=False)
    created_by = Column(String(100), nullable=False)
    created_at = Column(String(30), nullable=False)
    updated_at = Column(String(30), nullable=False)
    priority = Column(String(10), default="MEDIA")
    pcl_percentage = Column(Float, nullable=True)
    requested_documents = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)

    # TRAZABILIDAD
    fecha_asignacion_pcl = Column(String(30), nullable=True)
    fecha_calificacion = Column(String(30), nullable=True)
    accion_pcl = Column(String(100), nullable=True)
    fecha_solicitud_documentos = Column(String(30), nullable=True)
    fecha_asignacion_comite = Column(String(30), nullable=True)
    fecha_visado = Column(String(30), nullable=True)
    accion_comite = Column(String(100), nullable=True)
    fecha_notificacion_axa = Column(String(30), nullable=True)

class UserModel(Base):
    __tablename__ = "usuarios_pcl"

    email = Column(String(100), primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    password = Column(String(100), nullable=False)
    role = Column(String(50), nullable=False)

class AuditModel(Base):
    __tablename__ = "auditoria_logs"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, nullable=False)
    user_name = Column(String(100), nullable=False)
    user_email = Column(String(100), nullable=False)
    user_role = Column(String(50), nullable=False)
    action = Column(String(100), nullable=False)
    origin_state = Column(String(50), nullable=False)
    destination_state = Column(String(50), nullable=False)
    origin_sub_step = Column(String(50), nullable=False)
    destination_sub_step = Column(String(50), nullable=False)
    timestamp = Column(String(30), nullable=False)
    comments = Column(Text, nullable=False)
    assigned_to_info = Column(String(100), nullable=True)

Base.metadata.create_all(bind=engine)

# SEMBRAR USUARIOS POR DEFECTO
db_init = SessionLocal()
if db_init.query(UserModel).count() == 0:
    db_init.add(UserModel(name="Estiven Ayala", email="estiven.ayala@codess.org.co", password="123456", role="ADMINISTRADOR"))
    db_init.add(UserModel(name="Karen Margarita Coba Macias", email="karen.coba@pcl.com", password="123456", role="MEDICO_CALIFICADOR"))
    db_init.add(UserModel(name="Nataly Ruiz", email="nataly.ruiz@pcl.com", password="123456", role="MEDICO_CALIFICADOR"))
    db_init.add(UserModel(name="Laura Vanessa Deyanira Rua Pertuz", email="laura.rua@pcl.com", password="123456", role="MEDICO_CALIFICADOR"))
    db_init.add(UserModel(name="Dr. Carlos Fernando Mora", email="carlos.mora@pcl.com", password="123456", role="MEDICO_COMITE"))
    db_init.add(UserModel(name="Yinibeth Paola Leyton Castro", email="yinibeth.leyton@pcl.com", password="123456", role="MEDICO_COMITE"))
    db_init.commit()

db_init.close()

# =============================================================
# APLICACIÓN FASTAPI Y LÓGICA DE NEGOCIO
# =============================================================

app = FastAPI(title="Sistema de Gestión PCL - Edición de Contraseñas")

ACTIVE_SESSIONS: Dict[str, str] = {}

class ModuleState(str, Enum):
    REGISTRO = "REGISTRO"
    CALIFICACION_PCL = "CALIFICACION_PCL"
    COMITE = "COMITE"
    PENDIENTE_CIERRE = "PENDIENTE_CIERRE"
    EN_SOLICITUD_DOCUMENTOS = "EN_SOLICITUD_DOCUMENTOS"
    CIERRE_ADMINISTRATIVO = "CIERRE_ADMINISTRATIVO"
    GESTIONADO = "GESTIONADO"

class SubStep(str, Enum):
    REGISTRADO = "REGISTRADO"
    ASIGNADO = "ASIGNADO"
    EVALUACION_CALIFICACION = "EVALUACION_CALIFICACION"
    CALIFICADO = "CALIFICADO"
    DEVOLUCION_ADMINISTRATIVA = "DEVOLUCION_ADMINISTRATIVA"
    EN_SOLICITUD_DOCUMENTOS = "EN_SOLICITUD_DOCUMENTOS"
    DOCUMENTOS_RECIBIDOS = "DOCUMENTOS_RECIBIDOS"
    EN_COMITE = "EN_COMITE"
    VISADO = "VISADO"
    DEVOLUCION_CALIFICADOR = "DEVOLUCION_CALIFICADOR"
    PENDIENTE_NOTIFICACION = "PENDIENTE_NOTIFICACION"
    NOTIFICADO = "NOTIFICADO"
    CIERRE_ADMINISTRATIVO = "CIERRE_ADMINISTRATIVO"
    GESTIONADO = "GESTIONADO"

class UserRole(str, Enum):
    ADMINISTRADOR = "ADMINISTRADOR"
    MEDICO_CALIFICADOR = "MEDICO_CALIFICADOR"
    MEDICO_COMITE = "MEDICO_COMITE"

STATE_TRANSITIONS_MATRIX = [
    {
        "current_state": ModuleState.REGISTRO, "current_sub_step": SubStep.REGISTRADO,
        "action_name": "Asignar Calificador Responsable",
        "condition_description": "Procede Gestión = SÍ. Caso registrado y listo para reparto técnico.",
        "destination_state": ModuleState.CALIFICACION_PCL, "destination_sub_step": SubStep.ASIGNADO,
        "requires_assignee": True, "requires_reason": False, "requires_percentage": False
    },
    {
        "current_state": ModuleState.CALIFICACION_PCL, "current_sub_step": SubStep.ASIGNADO,
        "action_name": "Dictaminar y Calificar Caso",
        "condition_description": "Procede a calificación = SÍ. Expediente suficiente. Pasa a Comité.",
        "destination_state": ModuleState.COMITE, "destination_sub_step": SubStep.CALIFICADO,
        "requires_percentage": True, "requires_reason": True, "requires_assignee": True
    },
    {
        "current_state": ModuleState.CALIFICACION_PCL, "current_sub_step": SubStep.ASIGNADO,
        "action_name": "Devolución Administrativa",
        "condition_description": "Procede a calificación = NO. Insuficiencia documental.",
        "destination_state": ModuleState.REGISTRO, "destination_sub_step": SubStep.DEVOLUCION_ADMINISTRATIVA,
        "requires_reason": True, "requires_percentage": False, "requires_assignee": False
    },
    {
        "current_state": ModuleState.REGISTRO, "current_sub_step": SubStep.DEVOLUCION_ADMINISTRATIVA,
        "action_name": "Solicitar Documentación",
        "condition_description": "Se requiere solicitud de documentos = SÍ. Pasa a Solicitud de Documentos.",
        "destination_state": ModuleState.EN_SOLICITUD_DOCUMENTOS, "destination_sub_step": SubStep.EN_SOLICITUD_DOCUMENTOS,
        "requires_documents_list": True, "requires_reason": True, "requires_percentage": False, "requires_assignee": False
    },
    {
        "current_state": ModuleState.REGISTRO, "current_sub_step": SubStep.DEVOLUCION_ADMINISTRATIVA,
        "action_name": "Cierre Administrativo",
        "condition_description": "Se requiere solicitud de documentos = NO. Finalización por causa administrativa.",
        "destination_state": ModuleState.CIERRE_ADMINISTRATIVO, "destination_sub_step": SubStep.CIERRE_ADMINISTRATIVO,
        "requires_reason": True, "requires_percentage": False, "requires_assignee": False
    },
    {
        "current_state": ModuleState.EN_SOLICITUD_DOCUMENTOS, "current_sub_step": SubStep.EN_SOLICITUD_DOCUMENTOS,
        "action_name": "Documentos Recibidos",
        "condition_description": "Soportes recibidos. Redirige a Calificación PCL con médico asignado.",
        "destination_state": ModuleState.CALIFICACION_PCL, "destination_sub_step": SubStep.ASIGNADO,
        "requires_assignee": True, "requires_reason": True, "requires_percentage": False
    },
    {
        "current_state": ModuleState.COMITE, "current_sub_step": SubStep.CALIFICADO,
        "action_name": "Aprobar Visado de Comité",
        "condition_description": "Procede a Visado = SÍ -> Dictamen ratificado y visado por la junta.",
        "destination_state": ModuleState.PENDIENTE_CIERRE, "destination_sub_step": SubStep.PENDIENTE_NOTIFICACION,
        "requires_reason": True, "requires_percentage": False, "requires_assignee": False
    },
    {
        "current_state": ModuleState.COMITE, "current_sub_step": SubStep.CALIFICADO,
        "action_name": "Devolución a Calificador",
        "condition_description": "Procede a Visado = NO -> Glosa técnica. Devuelve a Calificación PCL.",
        "destination_state": ModuleState.CALIFICACION_PCL, "destination_sub_step": SubStep.DEVOLUCION_CALIFICADOR,
        "requires_reason": True, "requires_percentage": False, "requires_assignee": False
    },
    {
        "current_state": ModuleState.CALIFICACION_PCL, "current_sub_step": SubStep.DEVOLUCION_CALIFICADOR,
        "action_name": "Ajustar y Recalificar (Re-evaluación)",
        "condition_description": "Procede a calificación = SÍ tras observaciones del Comité. Pasa a Comité.",
        "destination_state": ModuleState.COMITE, "destination_sub_step": SubStep.CALIFICADO,
        "requires_percentage": True, "requires_reason": True, "requires_assignee": True
    },
    {
        "current_state": ModuleState.CALIFICACION_PCL, "current_sub_step": SubStep.DEVOLUCION_CALIFICADOR,
        "action_name": "Devolución Administrativa tras Glosa",
        "condition_description": "Procede a calificación = NO. Imposible subsanar sin nueva documentación médica. Va a Registro.",
        "destination_state": ModuleState.REGISTRO, "destination_sub_step": SubStep.DEVOLUCION_ADMINISTRATIVA,
        "requires_reason": True, "requires_percentage": False, "requires_assignee": False
    },
    {
        "current_state": ModuleState.PENDIENTE_CIERRE, "current_sub_step": SubStep.PENDIENTE_NOTIFICACION,
        "action_name": "Registrar Notificación Exitosa",
        "condition_description": "Notificado debidamente con constancia. Pasa a Gestionado.",
        "destination_state": ModuleState.GESTIONADO, "destination_sub_step": SubStep.GESTIONADO,
        "requires_reason": True, "requires_percentage": False, "requires_assignee": False
    }
]

# API USUARIOS - CREAR
@app.post("/api/users/create")
def create_user(
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(...)
):
    db = SessionLocal()
    clean_email = email.strip().lower()
    existing = db.query(UserModel).filter(UserModel.email == clean_email).first()
    if existing:
        db.close()
        raise HTTPException(status_code=400, detail="El correo electrónico ya se encuentra registrado.")
    
    new_user = UserModel(name=name.strip(), email=clean_email, password=password.strip(), role=role)
    db.add(new_user)
    db.commit()
    db.close()
    return {"success": True}

# API USUARIOS - EDITAR PERMISOS Y CONTRASEÑA
@app.post("/api/users/update")
def update_user_role_and_pass(
    email: str = Form(...),
    role: str = Form(...),
    password: Optional[str] = Form(None)
):
    db = SessionLocal()
    clean_email = email.strip().lower()
    user = db.query(UserModel).filter(UserModel.email == clean_email).first()
    if not user:
        db.close()
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    
    user.role = role
    if password and password.strip():
        user.password = password.strip()

    db.commit()
    db.close()
    return {"success": True}

# API USUARIOS - ELIMINAR USUARIO
@app.post("/api/users/delete")
def delete_user(
    email: str = Form(...),
    active_email: str = Form(...)
):
    clean_email = email.strip().lower()
    if clean_email == active_email.strip().lower():
        raise HTTPException(status_code=400, detail="No puedes eliminar tu propio usuario activo.")
    
    db = SessionLocal()
    user = db.query(UserModel).filter(UserModel.email == clean_email).first()
    if not user:
        db.close()
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    
    db.delete(user)
    db.commit()
    db.close()
    return {"success": True}

# LOGIN
@app.post("/login")
def login(email: str = Form(...), password: str = Form(...)):
    db = SessionLocal()
    clean_email = email.strip().lower()
    user = db.query(UserModel).filter(UserModel.email == clean_email).first()
    
    if not user or user.password != password.strip():
        db.close()
        return HTMLResponse(
            """<script>alert('Correo o contraseña incorrectos.'); window.location.href='/login-view';</script>"""
        )
    
    db.close()
    session_id = f"session_{clean_email}"
    ACTIVE_SESSIONS[session_id] = clean_email
    return RedirectResponse(url=f"/?session={session_id}", status_code=303)

@app.get("/login-view", response_class=HTMLResponse)
def login_view():
    return """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>Inicio de Sesión - Sistema de Gestión PCL</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
        <style> body { font-family: 'Inter', sans-serif; } </style>
    </head>
    <body class="bg-slate-900 flex items-center justify-center min-h-screen p-4">
        <div class="bg-white p-8 rounded-2xl shadow-2xl w-full max-w-md border border-slate-200">
            <div class="text-center mb-6">
                <div class="w-12 h-12 bg-indigo-600 rounded-2xl mx-auto flex items-center justify-center text-white text-xl font-bold mb-3 shadow-lg">⚡</div>
                <h2 class="text-xl font-bold text-slate-900 tracking-tight">Sistema de Gestión PCL</h2>
                <p class="text-xs text-slate-500 font-medium mt-1">Ingrese sus credenciales de acceso institucional</p>
            </div>
            
            <form action="/login" method="post" class="space-y-4">
                <div>
                    <label class="block text-xs font-bold text-slate-700 mb-1">Correo Electrónico *</label>
                    <input type="email" name="email" required placeholder="ejemplo@codess.org.co" class="w-full px-3.5 py-2.5 text-xs rounded-xl border border-slate-300 font-medium text-slate-800 focus:ring-2 focus:ring-indigo-500 outline-none">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-700 mb-1">Contraseña *</label>
                    <input type="password" name="password" required placeholder="••••••••" class="w-full px-3.5 py-2.5 text-xs rounded-xl border border-slate-300 font-medium text-slate-800 focus:ring-2 focus:ring-indigo-500 outline-none">
                </div>
                <button type="submit" class="w-full py-3 bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-xs rounded-xl shadow-md transition-all">Iniciar Sesión &rarr;</button>
            </form>
        </div>
    </body>
    </html>
    """

# EXPORTAR EXCEL ESTADOS
@app.get("/api/export-excel-estados")
def export_excel_estados():
    db = SessionLocal()
    cases = db.query(CaseModel).all()
    output = io.BytesIO()
    cases_data = [{
        "ID Caso": str(c.id), "Paciente": c.patient_name, "Tipo Doc": c.document_type,
        "N° Doc": c.patient_id, "# Siniestro": c.claim_number or "PENDIENTE", "Origen": c.origin_type or "Laboral",
        "Evento": c.event_type or "AT", "Tipo Calificación": c.qualification_type, "Días IT": c.it_days or 0,
        "Módulo Actual": c.module_state, "Sub-Paso": c.sub_step, "Responsable Asignado": c.assigned_to,
        "% PCL Dictamen": f"{c.pcl_percentage}%" if c.pcl_percentage is not None else "--",
        "Fecha Radicación AXA": c.axa_filing_date, "Fecha creacion App": c.created_at,
        "Fecha Asigancion PCL": c.fecha_asignacion_pcl or "N/A", "Fecha Calificacion": c.fecha_calificacion or "N/A",
        "Accion PCL": c.accion_pcl or "N/A", "Fecha solicitud de documentos": c.fecha_solicitud_documentos or "N/A",
        "Documentos solicitados": c.requested_documents or "N/A", "Fecha Asignacion Comité": c.fecha_asignacion_comite or "N/A",
        "Fecha Visado": c.fecha_visado or "N/A", "Accion Comité": c.accion_comite or "N/A",
        "Fecha Notificacion AXA": c.fecha_notificacion_axa or "N/A", "Ultima Modificacion": c.updated_at
    } for c in cases]
    db.close()

    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.DataFrame(cases_data).to_excel(writer, sheet_name='Estados_Casos_PCL', index=False)

    output.seek(0)
    filename = f"Casos_PCL_Estados_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
    return StreamingResponse(
        output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

# EXPORTAR EXCEL AUDITORÍA
@app.get("/api/export-excel-auditoria")
def export_excel_auditoria():
    db = SessionLocal()
    audits = db.query(AuditModel).order_by(AuditModel.id.desc()).all()
    output = io.BytesIO()
    audit_data = [{
        "ID Evento": str(a.id), "ID Caso": str(a.case_id), "Fecha Exacta": a.timestamp,
        "Usuario Responsable": a.user_name, "Email": a.user_email, "Rol": a.user_role,
        "Acción Realizada": a.action, "Asignado A": a.assigned_to_info or "N/A",
        "Módulo Origen": a.origin_state, "Sub-Paso Origen": a.origin_sub_step,
        "Módulo Destino": a.destination_state, "Sub-Paso Destino": a.destination_sub_step,
        "Observaciones / Motivo": a.comments
    } for a in audits]
    db.close()

    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.DataFrame(audit_data).to_excel(writer, sheet_name='Log_Auditoria_Movimientos', index=False)

    output.seek(0)
    filename = f"Auditoria_PCL_Logs_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
    return StreamingResponse(
        output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.get("/api/cases/{case_id}")
def get_case_detail(case_id: str):
    db = SessionLocal()
    case = db.query(CaseModel).filter(CaseModel.id == int(case_id)).first()
    if not case:
        db.close()
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    
    rules = [r for r in STATE_TRANSITIONS_MATRIX if r["current_state"] == case.module_state and r["current_sub_step"] == case.sub_step]
    audits = db.query(AuditModel).filter(AuditModel.case_id == int(case_id)).order_by(AuditModel.id.desc()).all()
    
    case_dict = {c.name: getattr(case, c.name) for c in case.__table__.columns}
    case_dict["id"] = str(case_dict["id"])
    audit_list = [{c.name: getattr(a, c.name) for c in a.__table__.columns} for a in audits]
    for a in audit_list:
        a["id"] = str(a["id"])
        a["case_id"] = str(a["case_id"])

    db.close()
    return {"case": case_dict, "rules": rules, "audits": audit_list}

@app.post("/api/cases/transition")
def transition_case(
    case_id: str = Form(...), action_name: str = Form(...), comments: str = Form(...),
    new_assignee: Optional[str] = Form(None), pcl_percentage: Optional[float] = Form(None),
    requested_docs: Optional[str] = Form(None),
    claim_number: Optional[str] = Form(None),
    origin_type: Optional[str] = Form(None),
    event_type: Optional[str] = Form(None),
    it_days: Optional[int] = Form(None),
    active_email: Optional[str] = Form("estiven.ayala@codess.org.co")
):
    db = SessionLocal()
    case = db.query(CaseModel).filter(CaseModel.id == int(case_id)).first()
    if not case:
        db.close()
        raise HTTPException(status_code=404, detail="Caso no encontrado.")

    rule = next((r for r in STATE_TRANSITIONS_MATRIX if r["action_name"] == action_name and r["current_state"] == case.module_state), None)
    if not rule:
        db.close()
        raise HTTPException(status_code=400, detail="Transición no permitida según la matriz de estados.")

    if rule.get("requires_reason") and not comments.strip():
        db.close()
        raise HTTPException(status_code=400, detail="Es obligatorio ingresar las observaciones de auditoría.")

    u_data = db.query(UserModel).filter(UserModel.email == active_email.strip().lower()).first()
    u_name = u_data.name if u_data else "Estiven Ayala"
    u_email = u_data.email if u_data else "estiven.ayala@codess.org.co"
    u_role = u_data.role if u_data else "ADMINISTRADOR"

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    origin_state = case.module_state
    origin_sub_step = case.sub_step

    case.module_state = rule["destination_state"].value if isinstance(rule["destination_state"], ModuleState) else str(rule["destination_state"])
    case.sub_step = rule["destination_sub_step"].value if isinstance(rule["destination_sub_step"], SubStep) else str(rule["destination_sub_step"])
    case.updated_at = now_str

    if claim_number and claim_number.strip(): case.claim_number = claim_number.strip()
    if origin_type and origin_type.strip(): case.origin_type = origin_type.strip()
    if event_type and event_type.strip(): case.event_type = event_type.strip()
    if it_days is not None: case.it_days = it_days

    assigned_info = None
    if rule.get("requires_assignee") and new_assignee and new_assignee.strip():
        case.assigned_to = new_assignee
        assigned_info = new_assignee
    elif case.module_state == "REGISTRO":
        case.assigned_to = "Lic. Paula Andrea Gómez"

    if case.module_state == "CALIFICACION_PCL":
        case.fecha_asignacion_pcl = now_str

    if case.module_state == "COMITE":
        case.fecha_asignacion_comite = now_str

    if action_name in ["Dictaminar y Calificar Caso", "Ajustar y Recalificar (Re-evaluación)", "Devolución Administrativa", "Devolución Administrativa tras Glosa"]:
        case.fecha_calificacion = now_str
        case.accion_pcl = action_name
        if rule.get("requires_percentage") and pcl_percentage is not None:
            case.pcl_percentage = pcl_percentage

    elif action_name == "Solicitar Documentación":
        case.fecha_solicitud_documentos = now_str
        if requested_docs:
            case.requested_documents = requested_docs

    elif action_name in ["Aprobar Visado de Comité", "Devolución a Calificador"]:
        case.fecha_visado = now_str
        case.accion_comite = action_name

    elif action_name == "Registrar Notificación Exitosa":
        case.fecha_notificacion_axa = now_str

    audit = AuditModel(
        case_id=case.id, user_name=u_name, user_email=u_email, user_role=u_role,
        action=action_name, origin_state=origin_state, destination_state=case.module_state,
        origin_sub_step=origin_sub_step, destination_sub_step=case.sub_step,
        timestamp=now_str, comments=comments, assigned_to_info=assigned_info
    )
    db.add(audit)
    db.commit()

    case_dict = {c.name: getattr(case, c.name) for c in case.__table__.columns}
    case_dict["id"] = str(case_dict["id"])
    db.close()
    return {"success": True, "case": case_dict}

# FORMULARIO REDUCIDO Y SIMPLIFICADO DE RADICACIÓN DE CASO
@app.post("/api/cases/create")
def create_case(
    document_type: str = Form(...),
    patient_id: str = Form(...),
    patient_name: str = Form(...),
    qualification_type: str = Form(...),
    axa_filing_date: str = Form(...),
    assigned_doctor: str = Form(...),
    active_email: Optional[str] = Form("estiven.ayala@codess.org.co")
):
    if not patient_id.isdigit():
        raise HTTPException(status_code=400, detail="El Número de Documento debe contener únicamente números (0-9).")
    if not axa_filing_date.strip():
        raise HTTPException(status_code=400, detail="La Fecha de Radicación AXA es obligatoria.")

    db = SessionLocal()
    u_data = db.query(UserModel).filter(UserModel.email == active_email.strip().lower()).first()
    u_name = u_data.name if u_data else "Estiven Ayala"
    u_email = u_data.email if u_data else "estiven.ayala@codess.org.co"
    u_role = u_data.role if u_data else "ADMINISTRADOR"

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    new_case = CaseModel(
        document_type=document_type,
        patient_id=patient_id,
        patient_name=patient_name.upper(),
        claim_number="PENDIENTE",
        origin_type="Laboral",
        event_type="AT",
        qualification_type=qualification_type,
        it_days=0,
        company_name="NO ESPECIFICADO",
        company_id=None,
        axa_filing_date=axa_filing_date,
        module_state="CALIFICACION_PCL",
        sub_step="ASIGNADO",
        assigned_to=assigned_doctor,
        assigned_role="MEDICO_CALIFICADOR",
        created_by=u_email,
        created_at=now_str,
        updated_at=now_str,
        pcl_percentage=None,
        fecha_asignacion_pcl=now_str
    )
    db.add(new_case)
    db.commit()
    db.refresh(new_case)

    audit = AuditModel(
        case_id=new_case.id, user_name=u_name, user_email=u_email, user_role=u_role,
        action="REGISTRAR_Y_ASIGNAR_CASO", origin_state="REGISTRO", destination_state="CALIFICACION_PCL",
        origin_sub_step="REGISTRADO", destination_sub_step="ASIGNADO", timestamp=now_str,
        comments=f"Creación formal e ingreso directo a Calificación PCL. Asignado a: {assigned_doctor}.",
        assigned_to_info=assigned_doctor
    )
    db.add(audit)
    db.commit()

    created_id = str(new_case.id)
    db.close()
    return {"success": True, "id": created_id}

# FRONTEND PRINCIPAL
@app.get("/", response_class=HTMLResponse)
def serve_ui(session: Optional[str] = None):
    if not session or session not in ACTIVE_SESSIONS:
        return RedirectResponse(url="/login-view")

    user_email = ACTIVE_SESSIONS[session]
    db = SessionLocal()
    current_u = db.query(UserModel).filter(UserModel.email == user_email).first()
    
    if not current_u:
        current_u_name = "Estiven Ayala"
        current_u_email = "estiven.ayala@codess.org.co"
        role_str = "ADMINISTRADOR"
    else:
        current_u_name = current_u.name
        current_u_email = current_u.email
        role_str = str(current_u.role)

    is_admin = (role_str == "ADMINISTRADOR")
    is_calificador = (role_str == "MEDICO_CALIFICADOR")
    is_comite = (role_str == "MEDICO_COMITE")

    cases = db.query(CaseModel).order_by(CaseModel.id.desc()).all()
    audits = db.query(AuditModel).order_by(AuditModel.id.desc()).all()
    users = db.query(UserModel).all()

    # MEDICOS EXCLUSIVOS POR ROL (EXCLUYE ADMINISTRADORES)
    medicos_pcl_db = [u.name for u in users if u.role == "MEDICO_CALIFICADOR"]
    medicos_comite_db = [u.name for u in users if u.role == "MEDICO_COMITE"]

    options_pcl_doc = "".join([f'<option value="{name}">{name}</option>' for name in medicos_pcl_db])
    options_comite_doc = "".join([f'<option value="{name}">{name}</option>' for name in medicos_comite_db])

    en_tramite = len([c for c in cases if c.module_state not in ["CIERRE_ADMINISTRATIVO", "GESTIONADO"]])
    finalizados = len(cases) - en_tramite

    def render_cases_cards(state_filter: Optional[List[str]] = None):
        filtered = [c for c in cases if c.module_state in state_filter] if state_filter else cases
        if not filtered:
            return '<div class="col-span-3 text-center py-8 text-xs text-slate-500 bg-white rounded-xl border border-slate-200">No hay expedientes activos en esta bandeja.</div>'
        
        cards = ""
        for c in filtered:
            pcl_val = f"{c.pcl_percentage}%" if (c.pcl_percentage is not None and c.pcl_percentage != "") else "--"
            
            cards += f"""
            <div class="bg-white rounded-xl border border-slate-200 hover:border-indigo-400 p-4 shadow-xs hover:shadow-md transition-all flex flex-col justify-between">
                <div>
                    <div class="flex items-center justify-between gap-2 mb-2">
                        <span class="font-mono font-bold text-sm text-indigo-700">ID Caso: {c.id}</span>
                        <span class="text-[10px] font-bold px-2 py-0.5 rounded border bg-sky-50 text-sky-700 border-sky-200">{c.module_state}</span>
                    </div>
                    <h4 class="text-sm font-bold text-slate-900 uppercase">{c.patient_name}</h4>
                    <div class="text-xs text-slate-500 font-mono mt-0.5">{c.document_type}: <strong>{c.patient_id}</strong> &bull; Sin. <strong class="text-indigo-700">#{c.claim_number or 'PENDIENTE'}</strong></div>
                    <div class="flex items-center gap-1.5 mt-2 flex-wrap">
                        <span class="text-[10px] font-bold px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 border">{c.qualification_type}</span>
                        <span class="text-[10px] font-mono font-bold px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-700 border">{c.event_type or 'AT'} - {c.origin_type or 'Laboral'}</span>
                        <span class="text-[10px] font-mono px-1.5 py-0.5 rounded bg-amber-50 text-amber-800 border">{c.it_days or 0} Días IT</span>
                        <span class="text-[10px] font-bold px-1.5 py-0.5 rounded bg-violet-50 text-violet-800 border">PCL: {pcl_val}</span>
                    </div>
                </div>
                <div class="pt-3 mt-3 border-t border-slate-100 flex items-center justify-between text-[11px] text-slate-500">
                    <span>👤 {c.assigned_to}</span>
                    <button onclick="openManageModal('{c.id}')" class="text-indigo-600 font-bold hover:underline">Gestionar &rarr;</button>
                </div>
            </div>
            """
        return cards

    users_rows = ""
    for u in users:
        is_self = (u.email == current_u_email)
        btn_delete = "" if is_self else f'<button onclick="deleteUser(\'{u.email}\')" class="px-2.5 py-1 rounded bg-rose-50 text-rose-700 border border-rose-200 font-bold hover:bg-rose-100 transition-all">🗑️ Eliminar</button>'
        
        users_rows += f"""
        <tr class="border-b hover:bg-slate-50/50">
            <td class="py-3 px-4 font-bold text-slate-800">{u.name}</td>
            <td class="py-3 px-4 font-mono text-slate-600">{u.email}</td>
            <td class="py-3 px-4">
                <span class="px-2 py-0.5 rounded text-[10px] font-bold bg-indigo-50 text-indigo-700 border border-indigo-200">{u.role}</span>
            </td>
            <td class="py-3 px-4 flex items-center gap-2">
                <button onclick="editUserRole('{u.email}', '{u.role}', '{u.name}')" class="px-2.5 py-1 rounded bg-indigo-50 text-indigo-700 border border-indigo-200 font-bold hover:bg-indigo-100 transition-all">✏️ Editar Permisos / Clave</button>
                {btn_delete}
            </td>
        </tr>
        """

    db.close()

    return f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>Sistema de Gestión PCL</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
        <style> body {{ font-family: 'Inter', sans-serif; }} </style>
    </head>
    <body class="bg-slate-100 text-slate-900 min-h-screen flex flex-col">
        <header class="bg-white border-b border-slate-200 sticky top-0 z-30 shadow-xs">
            <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-3">
                <div class="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
                    <div class="flex items-center space-x-3">
                        <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-600 via-indigo-700 to-blue-800 flex items-center justify-center text-white shadow-md font-bold">⚡</div>
                        <div>
                            <div class="flex items-center gap-2">
                                <h1 class="text-xl font-bold text-slate-900 tracking-tight">Sistema de Gestión PCL</h1>
                                <span class="text-xs font-semibold px-2 py-0.5 rounded-md bg-indigo-50 text-indigo-700 border border-indigo-200">{role_str}</span>
                            </div>
                            <p class="text-xs text-slate-500 font-medium">Usuario: <strong class="text-indigo-700">{current_u_name}</strong> ({current_u_email})</p>
                        </div>
                    </div>
                    <div class="flex flex-wrap items-center gap-2.5">
                        <div class="flex items-center gap-2 bg-slate-50 px-3 py-1.5 rounded-lg border border-slate-200 text-xs">
                            <span class="w-2 h-2 rounded-full bg-amber-500 animate-pulse"></span>
                            <span>En trámite: <strong>{en_tramite}</strong></span>
                            <span class="text-slate-300">|</span>
                            <span class="w-2 h-2 rounded-full bg-emerald-500"></span>
                            <span>Finalizados: <strong>{finalizados}</strong></span>
                        </div>
                        {f'<button onclick="document.getElementById(\'modal-nuevo\').classList.remove(\'hidden\')" class="px-3.5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold shadow-xs">+ Nuevo Caso</button>' if is_admin else ''}
                        
                        <a href="/api/export-excel-estados" class="px-3 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold shadow-xs">📄 Excel Estados</a>
                        {f'<a href="/api/export-excel-auditoria" class="px-3 py-2 rounded-lg bg-indigo-800 hover:bg-indigo-900 text-white text-xs font-semibold shadow-xs">📜 Excel Auditoría</a>' if is_admin else ''}
                        <a href="/login-view" class="px-3 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-semibold border">🚪 Cerrar Sesión</a>
                    </div>
                </div>
                
                <div class="mt-3 pt-2.5 border-t border-slate-100 flex items-center justify-between gap-2 overflow-x-auto">
                    <div class="flex items-center space-x-1.5">
                        {f'<button onclick="switchTab(\'mod-nuevos\')" id="btn-mod-nuevos" class="tab-btn px-3 py-2 rounded-xl text-xs font-bold bg-slate-900 text-white shadow-xs whitespace-nowrap">✨ Casos Nuevos</button>' if is_admin else ''}
                        {f'<button onclick="switchTab(\'mod-admin\')" id="btn-mod-admin" class="tab-btn px-3 py-2 rounded-xl text-xs font-bold bg-white text-slate-700 border border-slate-200 whitespace-nowrap">📁 Gestión Admin</button>' if is_admin else ''}
                        {f'<button onclick="switchTab(\'mod-calificacion\')" id="btn-mod-calificacion" class="tab-btn px-3 py-2 rounded-xl text-xs font-bold {"bg-slate-900 text-white shadow-xs" if is_calificador else "bg-white text-slate-700 border border-slate-200"} whitespace-nowrap">🩺 Calificación PCL</button>' if is_admin or is_calificador else ''}
                        {f'<button onclick="switchTab(\'mod-comite\')" id="btn-mod-comite" class="tab-btn px-3 py-2 rounded-xl text-xs font-bold {"bg-slate-900 text-white shadow-xs" if is_comite else "bg-white text-slate-700 border border-slate-200"} whitespace-nowrap">👥 Comité</button>' if is_admin or is_comite else ''}
                        {f'<button onclick="switchTab(\'mod-cierre\')" id="btn-mod-cierre" class="tab-btn px-3 py-2 rounded-xl text-xs font-bold bg-white text-slate-700 border border-slate-200 whitespace-nowrap">📤 Pendiente Cierre</button>' if is_admin else ''}
                    </div>
                    {f'''
                    <div class="flex items-center space-x-1 pl-2 border-l border-slate-200">
                        <button onclick="switchTab('mod-usuarios')" id="btn-mod-usuarios" class="tab-btn px-2.5 py-1.5 rounded-lg text-xs font-bold bg-indigo-50 text-indigo-700 border border-indigo-200 whitespace-nowrap">⚙️ Gestión Usuarios</button>
                        <button onclick="switchTab('mod-finalizados')" id="btn-mod-finalizados" class="tab-btn px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-white text-slate-600 whitespace-nowrap">📦 Finalizados</button>
                        <button onclick="switchTab('mod-auditoria')" id="btn-mod-auditoria" class="tab-btn px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-white text-slate-600 whitespace-nowrap">📜 Auditoría General</button>
                    </div>
                    ''' if is_admin else ''}
                </div>
            </div>
        </header>

        <main class="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6">
            {f'''
            <div id="mod-nuevos" class="tab-content space-y-4">
                <div class="bg-gradient-to-br from-indigo-50 via-white to-indigo-50/40 p-5 rounded-2xl border border-indigo-100 flex items-center justify-between">
                    <div>
                        <h3 class="text-sm font-bold text-slate-900">✨ Ventanilla Única de Radicación e Ingreso Pericial</h3>
                        <p class="text-xs text-slate-600 mt-1">Ingreso automático de casos con ID consecutivo numérico simple (1, 2, 3...).</p>
                    </div>
                    <button onclick="document.getElementById('modal-nuevo').classList.remove('hidden')" class="px-4 py-2 rounded-xl bg-indigo-600 text-white text-xs font-semibold">Abrir Formulario de Ingreso</button>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">{render_cases_cards(["REGISTRO"])}</div>
            </div>

            <div id="mod-admin" class="tab-content hidden space-y-4">
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">{render_cases_cards(["REGISTRO", "EN_SOLICITUD_DOCUMENTOS"])}</div>
            </div>
            ''' if is_admin else ''}

            {f'''
            <div id="mod-calificacion" class="tab-content {"space-y-4" if is_calificador else "hidden space-y-4"}">
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">{render_cases_cards(["CALIFICACION_PCL"])}</div>
            </div>
            ''' if is_admin or is_calificador else ''}

            {f'''
            <div id="mod-comite" class="tab-content {"space-y-4" if is_comite else "hidden space-y-4"}">
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">{render_cases_cards(["COMITE"])}</div>
            </div>
            ''' if is_admin or is_comite else ''}

            {f'''
            <div id="mod-cierre" class="tab-content hidden space-y-4">
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">{render_cases_cards(["PENDIENTE_CIERRE"])}</div>
            </div>

            <div id="mod-usuarios" class="tab-content hidden space-y-6">
                <div class="bg-white p-6 rounded-2xl border border-slate-200 shadow-xs space-y-4">
                    <h3 class="text-sm font-bold text-slate-900 border-b pb-2">➕ Registrar Nuevo Usuario y Asignar Permisos</h3>
                    <form id="form-user" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs">
                        <div>
                            <label class="block font-bold text-slate-700 mb-1">Nombre Completo *</label>
                            <input type="text" name="name" required placeholder="Ej: Dr. Roberto Gómez" class="w-full px-3 py-2 rounded-lg border border-slate-300">
                        </div>
                        <div>
                            <label class="block font-bold text-slate-700 mb-1">Correo Electrónico *</label>
                            <input type="email" name="email" required placeholder="roberto.gomez@pcl.com" class="w-full px-3 py-2 rounded-lg border border-slate-300">
                        </div>
                        <div>
                            <label class="block font-bold text-slate-700 mb-1">Contraseña *</label>
                            <input type="password" name="password" required placeholder="••••••••" class="w-full px-3 py-2 rounded-lg border border-slate-300">
                        </div>
                        <div>
                            <label class="block font-bold text-slate-700 mb-1">Perfil / Rol *</label>
                            <select name="role" class="w-full px-3 py-2 rounded-lg border border-slate-300 font-semibold text-indigo-700">
                                <option value="MEDICO_CALIFICADOR">🩺 Médico Calificador PCL</option>
                                <option value="MEDICO_COMITE">👥 Médico Comité</option>
                                <option value="ADMINISTRADOR">🔑 Administrador (Acceso Total)</option>
                            </select>
                        </div>
                        <div class="sm:col-span-2 lg:col-span-4 flex justify-end">
                            <button type="submit" class="px-5 py-2 rounded-xl bg-indigo-600 text-white font-bold text-xs shadow-xs hover:bg-indigo-700">Guardar Nuevo Usuario</button>
                        </div>
                    </form>
                </div>

                <div class="bg-white rounded-2xl border border-slate-200 overflow-hidden shadow-xs">
                    <div class="p-4 bg-slate-50 border-b">
                        <h4 class="text-xs font-bold uppercase text-slate-700">Directorio de Usuarios Activos en el Sistema</h4>
                    </div>
                    <table class="w-full text-left text-xs text-slate-700">
                        <thead class="bg-slate-50 text-[11px] uppercase font-bold border-b">
                            <tr><th class="py-3 px-4">Usuario</th><th class="py-3 px-4">Correo Electrónico</th><th class="py-3 px-4">Perfil / Permisos</th><th class="py-3 px-4">Acciones</th></tr>
                        </thead>
                        <tbody>
                            {users_rows}
                        </tbody>
                    </table>
                </div>
            </div>

            <div id="mod-finalizados" class="tab-content hidden space-y-4">
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">{render_cases_cards(["CIERRE_ADMINISTRATIVO", "GESTIONADO"])}</div>
            </div>

            <div id="mod-auditoria" class="tab-content hidden space-y-4">
                <div class="bg-white rounded-xl border overflow-hidden shadow-xs">
                    <table class="w-full text-left text-xs text-slate-700">
                        <thead class="bg-slate-50 text-[11px] uppercase font-bold border-b">
                            <tr><th class="py-3 px-4">ID Evento</th><th class="py-3 px-4">ID Caso</th><th class="py-3 px-4">Fecha</th><th class="py-3 px-4">Usuario</th><th class="py-3 px-4">Acción</th><th class="py-3 px-4">Transición</th><th class="py-3 px-4">Observaciones</th></tr>
                        </thead>
                        <tbody>
                            {"".join([f'<tr class="border-b"><td class="py-3 px-4 font-mono font-bold text-slate-500">{a.id}</td><td class="py-3 px-4 font-mono font-bold text-indigo-700">{a.case_id}</td><td class="py-3 px-4">{a.timestamp}</td><td class="py-3 px-4">{a.user_name}</td><td class="py-3 px-4 font-semibold">{a.action}</td><td class="py-3 px-4"><span class="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-100">{a.origin_state} &rarr; {a.destination_state}</span></td><td class="py-3 px-4 text-slate-600">{a.comments}</td></tr>' for a in audits])}
                        </tbody>
                    </table>
                </div>
            </div>
            ''' if is_admin else ''}
        </main>

        <!-- MODAL EDITAR PERMISOS/ROL Y CONTRASEÑA DE USUARIO -->
        <div id="modal-edit-user" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-xs hidden">
            <div class="bg-white rounded-2xl shadow-2xl border border-slate-200 w-full max-w-md p-6 space-y-4">
                <div class="flex justify-between items-center border-b pb-2">
                    <h3 class="text-sm font-bold text-slate-900">✏️ Modificar Permisos y Contraseña</h3>
                    <button onclick="document.getElementById('modal-edit-user').classList.add('hidden')" class="font-bold text-slate-500 hover:text-slate-800">&times;</button>
                </div>
                <form id="form-edit-user" class="space-y-3 text-xs">
                    <input type="hidden" id="edit-user-email" name="email">
                    <div>
                        <label class="block font-bold text-slate-700 mb-1">Usuario</label>
                        <input type="text" id="edit-user-name" disabled class="w-full px-3 py-2 rounded-lg border bg-slate-100 text-slate-600 font-semibold">
                    </div>
                    <div>
                        <label class="block font-bold text-slate-700 mb-1">Nuevo Perfil / Rol de Acceso *</label>
                        <select id="edit-user-role" name="role" class="w-full px-3 py-2 rounded-lg border border-slate-300 font-bold text-indigo-700">
                            <option value="MEDICO_CALIFICADOR">🩺 Médico Calificador PCL</option>
                            <option value="MEDICO_COMITE">👥 Médico Comité</option>
                            <option value="ADMINISTRADOR">🔑 Administrador (Acceso Total)</option>
                        </select>
                    </div>
                    <div>
                        <label class="block font-bold text-slate-700 mb-1">Cambiar Contraseña (Opcional)</label>
                        <input type="password" id="edit-user-pass" name="password" placeholder="Dejar en blanco para mantener la actual" class="w-full px-3 py-2 rounded-lg border border-slate-300">
                    </div>
                    <div class="flex justify-end gap-2 pt-3 border-t">
                        <button type="button" onclick="document.getElementById('modal-edit-user').classList.add('hidden')" class="px-3 py-1.5 border rounded-lg font-semibold">Cancelar</button>
                        <button type="submit" class="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white font-bold rounded-lg shadow-xs">Guardar Cambios</button>
                    </div>
                </form>
            </div>
        </div>

        <!-- MODAL FORMULARIO DE INGRESO RADICACIÓN SIMPLIFICADO -->
        <div id="modal-nuevo" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-xs hidden">
            <div class="bg-white rounded-2xl shadow-2xl border border-slate-200 w-full max-w-2xl overflow-hidden">
                <div class="px-6 py-4 bg-slate-900 text-white flex items-center justify-between">
                    <h2 class="text-base font-bold">Radicar Nuevo Caso de Peritación PCL</h2>
                    <button onclick="document.getElementById('modal-nuevo').classList.add('hidden')" class="text-slate-400 hover:text-white">&times;</button>
                </div>
                
                <form id="form-case" class="p-6 space-y-4 max-h-[80vh] overflow-y-auto">
                    <input type="hidden" name="active_email" value="{current_u_email}">
                    <div class="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-3">
                        <h3 class="text-xs font-bold uppercase text-slate-800 border-b pb-1">1. Identificación del Paciente / Dictaminado</h3>
                        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                            <div>
                                <label class="block text-xs font-bold text-slate-700 mb-1">Tipo de Documento *</label>
                                <select name="document_type" class="w-full px-3 py-2 text-xs rounded-lg border border-slate-300">
                                    <option>Cédula de Ciudadanía</option>
                                    <option>Cédula de Extranjería</option>
                                    <option>Pasaporte</option>
                                    <option>Permiso de Protección Temporal - PPT</option>
                                    <option>Tarjeta de Identidad</option>
                                    <option>Registro Civil</option>
                                </select>
                            </div>
                            <div>
                                <label class="block text-xs font-bold text-slate-700 mb-1">Número de Documento (Solo 0-9) *</label>
                                <input type="text" name="patient_id" required onkeypress="return event.charCode >= 48 && event.charCode <= 57" placeholder="Ej: 1020485921" class="w-full px-3 py-2 text-xs font-mono rounded-lg border border-slate-300">
                            </div>
                            <div class="sm:col-span-2">
                                <label class="block text-xs font-bold text-slate-700 mb-1">Nombre Completo (AUTO-MAYÚSCULAS) *</label>
                                <input type="text" name="patient_name" required oninput="this.value = this.value.toUpperCase()" placeholder="EJ: LORENA PATRICIA SOLORZANO" class="w-full px-3 py-2 text-xs uppercase font-semibold rounded-lg border border-slate-300">
                            </div>
                        </div>
                    </div>

                    <div class="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-3">
                        <h3 class="text-xs font-bold uppercase text-slate-800 border-b pb-1">2. Parámetros Técnicos de Peritación PCL</h3>
                        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                            <div>
                                <label class="block text-xs font-bold text-slate-700 mb-1">Tipo de Calificación *</label>
                                <select name="qualification_type" class="w-full px-3 py-2 text-xs rounded-lg border border-slate-300">
                                    <option>ATEL</option><option>COMBO</option><option>REVISION</option><option>AMEEC</option><option>COMBO AST</option><option>COMUN</option><option>DTO</option><option>NORMAL</option><option>TUTELA</option><option>INTEGRAL</option>
                                </select>
                            </div>
                            <div>
                                <label class="block text-xs font-bold text-slate-700 mb-1">Fecha Radicación AXA *</label>
                                <input type="date" name="axa_filing_date" required class="w-full px-3 py-2 text-xs rounded-lg border border-slate-300 bg-white">
                            </div>
                            <div class="sm:col-span-2">
                                <label class="block text-xs font-bold text-slate-700 mb-1">Médico Calificador Asignado *</label>
                                <select name="assigned_doctor" class="w-full px-3 py-2 text-xs rounded-lg border border-slate-300 font-semibold text-indigo-700">
                                    {options_pcl_doc}
                                </select>
                            </div>
                        </div>
                    </div>

                    <div class="flex justify-end gap-2 pt-2 border-t">
                        <button type="button" onclick="document.getElementById('modal-nuevo').classList.add('hidden')" class="px-4 py-2 text-xs font-semibold rounded-lg border">Cancelar</button>
                        <button type="submit" class="px-5 py-2 text-xs font-bold rounded-lg bg-indigo-600 text-white">Crear y Radicar en Calificación PCL</button>
                    </div>
                </form>
            </div>
        </div>

        <!-- MODAL DINÁMICO DE GESTIÓN CON FORMULARIO PERICIAL EXTENDIDO -->
        <div id="modal-gestionar" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-xs hidden">
            <div class="bg-white rounded-2xl shadow-2xl border border-slate-200 w-full max-w-4xl overflow-hidden">
                <div class="px-6 py-4 bg-slate-900 text-white flex items-center justify-between">
                    <div>
                        <h2 id="m-case-id" class="text-lg font-bold">Caso #</h2>
                        <p id="m-patient-name" class="text-xs text-slate-300">PATIENT NAME</p>
                    </div>
                    <button onclick="document.getElementById('modal-gestionar').classList.add('hidden')" class="text-slate-400 hover:text-white">&times;</button>
                </div>

                <div class="flex border-b bg-slate-50 px-6 gap-2 pt-2 text-xs font-bold">
                    <button onclick="switchModalTab('m-tab-flow')" id="btn-m-flow" class="m-tab-btn py-2 px-3 border-b-2 border-indigo-600 text-indigo-700">Motor de Flujo</button>
                    <button onclick="switchModalTab('m-tab-details')" id="btn-m-details" class="m-tab-btn py-2 px-3 text-slate-500">Detalles del Expediente</button>
                    <button onclick="switchModalTab('m-tab-history')" id="btn-m-history" class="m-tab-btn py-2 px-3 text-slate-500">Historial de Movimientos</button>
                </div>

                <div class="p-6 space-y-4 max-h-[70vh] overflow-y-auto">
                    <div id="m-tab-flow" class="m-tab-content space-y-4">
                        <div id="m-rules-container" class="space-y-3">
                            <h4 class="text-xs font-bold uppercase tracking-wider text-slate-700 border-b pb-2">Transiciones Válidas</h4>
                            <div id="m-rules-list" class="grid grid-cols-1 md:grid-cols-2 gap-3"></div>
                        </div>

                        <form id="form-transition" class="hidden p-4 rounded-xl border border-indigo-200 bg-indigo-50/30 space-y-3">
                            <input type="hidden" id="trans-case-id" name="case_id">
                            <input type="hidden" id="trans-action-name" name="action_name">
                            <input type="hidden" name="active_email" value="{current_u_email}">

                            <div class="font-bold text-xs text-indigo-900" id="trans-title">Confirmar Transición</div>
                            
                            <!-- CAMPOS PERICIALES COMPLEMENTARIOS QUE SE COMPLETAN AL GESTIONAR -->
                            <div id="field-pericial-extra" class="space-y-3 p-3 bg-white rounded-lg border border-slate-200 text-xs">
                                <div class="font-bold text-slate-800 border-b pb-1">📋 Información Técnica de Peritación (Diligenciada por el Calificador PCL)</div>
                                <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                                    <div>
                                        <label class="block font-bold text-slate-700 mb-1"># Siniestro *</label>
                                        <input type="text" id="m-inp-claim" name="claim_number" required placeholder="Ej: 8920194" onkeypress="return event.charCode >= 48 && event.charCode <= 57" class="w-full px-3 py-1.5 rounded border border-slate-300 font-mono">
                                    </div>
                                    <div>
                                        <label class="block font-bold text-slate-700 mb-1">Tipo de Origen *</label>
                                        <select id="m-inp-origin" name="origin_type" class="w-full px-3 py-1.5 rounded border border-slate-300">
                                            <option value="Laboral">Laboral</option>
                                            <option value="Común">Común</option>
                                            <option value="Mixto">Mixto</option>
                                        </select>
                                    </div>
                                    <div>
                                        <label class="block font-bold text-slate-700 mb-1">Tipo de Evento *</label>
                                        <select id="m-inp-event" name="event_type" class="w-full px-3 py-1.5 rounded border border-slate-300">
                                            <option value="AT">AT</option>
                                            <option value="EL">EL</option>
                                        </select>
                                    </div>
                                    <div>
                                        <label class="block font-bold text-slate-700 mb-1">Días IT *</label>
                                        <input type="number" id="m-inp-it" name="it_days" required min="0" value="180" class="w-full px-3 py-1.5 rounded border border-slate-300">
                                    </div>
                                </div>
                            </div>

                            <div id="field-assignee" class="hidden">
                                <label class="block text-xs font-bold text-slate-800 mb-1" id="lbl-assignee">Seleccionar Integrante Responsable *</label>
                                <select id="sel-assignee" name="new_assignee" class="w-full px-3 py-2 text-xs rounded-lg border bg-white font-semibold text-slate-800">
                                    <!-- SE POBLA DINAMICAMENTE SEGUN LA ACCION -->
                                </select>
                            </div>

                            <div id="field-percentage" class="hidden">
                                <label class="block text-xs font-bold text-slate-800 mb-1">% PCL Dictaminado (Decreto 1507 de 2014) *</label>
                                <input type="number" step="0.01" name="pcl_percentage" value="25.00" class="w-40 px-3 py-2 text-xs font-bold rounded-lg border bg-white">
                            </div>

                            <div id="field-docs" class="hidden">
                                <label class="block text-xs font-bold text-slate-800 mb-1">Especificación de Documentación Requerida *</label>
                                <textarea name="requested_docs" rows="2" class="w-full px-3 py-2 text-xs rounded-lg border bg-white" placeholder="Detalle los exámenes e historias clínicas solicitadas..."></textarea>
                            </div>

                            <div>
                                <label class="block text-xs font-bold text-slate-800 mb-1">Observaciones / Justificación de la Acción (Trazabilidad de Auditoría) *</label>
                                <textarea name="comments" required rows="2" class="w-full px-3 py-2 text-xs rounded-lg border bg-white" placeholder="Ingrese las consideraciones clínicas o administrativas..."></textarea>
                            </div>

                            <div class="flex justify-end gap-2 pt-2">
                                <button type="button" onclick="document.getElementById('form-transition').classList.add('hidden')" class="px-3 py-1.5 text-xs font-semibold rounded-lg border bg-white">Cancelar</button>
                                <button type="submit" class="px-4 py-1.5 text-xs font-bold rounded-lg bg-indigo-600 text-white">Ejecutar Cambio de Estado y Registrar Auditoría</button>
                            </div>
                        </form>
                    </div>

                    <div id="m-tab-details" class="m-tab-content hidden space-y-4 text-xs">
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div class="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-2">
                                <h4 class="font-bold text-slate-800 uppercase border-b pb-1">Identificación del Paciente</h4>
                                <div><strong>Nombre:</strong> <span id="d-name"></span></div>
                                <div><strong>Documento:</strong> <span id="d-doc"></span></div>
                                <div><strong># Siniestro:</strong> <span id="d-claim" class="font-bold text-indigo-700"></span></div>
                                <div><strong>Fecha Radicación AXA:</strong> <span id="d-insurer" class="font-mono font-semibold text-slate-900"></span></div>
                            </div>
                            <div class="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-2">
                                <h4 class="font-bold text-slate-800 uppercase border-b pb-1">DATOS PERICIALES</h4>
                                <div><strong>Origen / Evento:</strong> <span id="d-origin"></span></div>
                                <div><strong>Días IT:</strong> <span id="d-it"></span></div>
                                <div><strong>% PCL Dictaminado:</strong> <span id="d-pcl" class="font-bold text-violet-700"></span></div>
                            </div>
                        </div>
                    </div>

                    <div id="m-tab-history" class="m-tab-content hidden space-y-3 text-xs">
                        <h4 class="font-bold text-slate-800 uppercase border-b pb-2">Trazabilidad de Movimientos del Caso</h4>
                        <div id="m-history-list" class="space-y-2"></div>
                    </div>
                </div>
            </div>
        </div>

        <script>
            const OPTIONS_COMITE = `{options_comite_doc}`;
            const OPTIONS_PCL = `{options_pcl_doc}`;

            function switchTab(tabId) {{
                document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
                document.querySelectorAll('.tab-btn').forEach(btn => {{
                    btn.classList.remove('bg-slate-900', 'text-white');
                    btn.classList.add('bg-white', 'text-slate-700', 'border', 'border-slate-200');
                }});

                document.getElementById(tabId).classList.remove('hidden');
                const activeBtn = document.getElementById('btn-' + tabId);
                if (activeBtn) {{
                    activeBtn.classList.remove('bg-white', 'text-slate-700', 'border', 'border-slate-200');
                    activeBtn.classList.add('bg-slate-900', 'text-white');
                }}
            }}

            function switchModalTab(mTabId) {{
                document.querySelectorAll('.m-tab-content').forEach(el => el.classList.add('hidden'));
                document.querySelectorAll('.m-tab-btn').forEach(btn => {{
                    btn.classList.remove('border-b-2', 'border-indigo-600', 'text-indigo-700');
                    btn.classList.add('text-slate-500');
                }});

                document.getElementById(mTabId).classList.remove('hidden');
                let btnId = 'btn-m-flow';
                if (mTabId === 'm-tab-details') btnId = 'btn-m-details';
                if (mTabId === 'm-tab-history') btnId = 'btn-m-history';

                const activeModalBtn = document.getElementById(btnId);
                if (activeModalBtn) {{
                    activeModalBtn.classList.add('border-b-2', 'border-indigo-600', 'text-indigo-700');
                }}
            }}

            function editUserRole(email, currentRole, name) {{
                document.getElementById('edit-user-email').value = email;
                document.getElementById('edit-user-name').value = name + ' (' + email + ')';
                document.getElementById('edit-user-role').value = currentRole;
                document.getElementById('edit-user-pass').value = '';
                document.getElementById('modal-edit-user').classList.remove('hidden');
            }}

            async function deleteUser(email) {{
                if (!confirm("¿Está seguro de que desea eliminar al usuario (" + email + ")? Esta acción no se puede deshacer.")) return;
                
                const formData = new FormData();
                formData.append('email', email);
                formData.append('active_email', '{current_u_email}');

                const res = await fetch('/api/users/delete', {{ method: 'POST', body: formData }});
                if (res.ok) {{
                    alert('Usuario eliminado correctamente.');
                    window.location.reload();
                }} else {{
                    const data = await res.json();
                    alert(data.detail || "Error al eliminar el usuario.");
                }}
            }}

            document.getElementById('form-edit-user').addEventListener('submit', async (e) => {{
                e.preventDefault();
                const formData = new FormData(e.target);
                const res = await fetch('/api/users/update', {{ method: 'POST', body: formData }});
                if (res.ok) {{
                    alert('Usuario actualizado correctamente.');
                    window.location.reload();
                }} else {{
                    const data = await res.json();
                    alert(data.detail || "Error al actualizar el usuario.");
                }}
            }});

            let currentCaseData = null;

            async function openManageModal(caseId) {{
                const res = await fetch('/api/cases/' + caseId);
                if (!res.ok) return;

                const data = await res.json();
                const c = data.case;
                currentCaseData = c;
                const rules = data.rules;
                const audits = data.audits;

                document.getElementById('m-case-id').innerText = "ID Caso: " + c.id + " (" + c.module_state + " - " + c.sub_step + ")";
                document.getElementById('m-patient-name').innerText = c.patient_name + " | C.C. " + c.patient_id;

                document.getElementById('d-name').innerText = c.patient_name;
                document.getElementById('d-doc').innerText = c.document_type + " " + c.patient_id;
                document.getElementById('d-claim').innerText = "#" + (c.claim_number || "PENDIENTE");
                document.getElementById('d-insurer').innerText = c.axa_filing_date ? c.axa_filing_date : "N/A";
                document.getElementById('d-origin').innerText = (c.origin_type || "Laboral") + " - " + (c.event_type || "AT") + " (" + c.qualification_type + ")";
                document.getElementById('d-it').innerText = (c.it_days || 0) + " Días";
                document.getElementById('d-pcl').innerText = (c.pcl_percentage !== null && c.pcl_percentage !== undefined) ? c.pcl_percentage + "%" : "--";

                const hList = document.getElementById('m-history-list');
                hList.innerHTML = "";
                if (audits.length === 0) {{
                    hList.innerHTML = '<div class="text-slate-500 italic">No hay registros de movimientos en la pista.</div>';
                }} else {{
                    audits.forEach(a => {{
                        const asigText = a.assigned_to_info ? ` &bull; <strong class="text-indigo-700">Asignado a: ${{a.assigned_to_info}}</strong>` : '';
                        hList.innerHTML += `
                        <div class="p-3 bg-slate-50 rounded-lg border border-slate-200">
                            <div class="flex justify-between items-center mb-1">
                                <span class="font-bold text-indigo-700">ID Evento: ${{a.id}} - ${{a.action}}</span>
                                <span class="font-mono text-[10px] text-slate-500">${{a.timestamp}}</span>
                            </div>
                            <div class="text-[11px] text-slate-600">
                                👤 <strong>${{a.user_name}}</strong> (${{a.user_role}}) &bull; Transición: <span class="font-semibold text-slate-800">${{a.origin_state}} &rarr; ${{a.destination_state}}</span>${{asigText}}
                            </div>
                            <div class="mt-1 text-slate-700 italic border-l-2 border-indigo-400 pl-2">
                                "${{a.comments}}"
                            </div>
                        </div>
                        `;
                    }});
                }}

                const rulesList = document.getElementById('m-rules-list');
                rulesList.innerHTML = "";

                if (rules.length === 0) {{
                    rulesList.innerHTML = '<div class="col-span-2 text-xs text-slate-500 italic p-3 bg-slate-50 rounded-lg border">Este caso se encuentra en un Estado Final (' + c.module_state + '). No hay más transiciones salientes.</div>';
                }} else {{
                    rules.forEach(r => {{
                        const reqAssignee = r.requires_assignee ? 'true' : 'false';
                        const reqPercentage = r.requires_percentage ? 'true' : 'false';
                        const reqDocs = r.requires_documents_list ? 'true' : 'false';

                        rulesList.innerHTML += `
                        <div onclick="selectRule('${{c.id}}', '${{r.action_name}}', ${{reqAssignee}}, ${{reqPercentage}}, ${{reqDocs}})" class="p-3.5 rounded-xl border border-slate-200 hover:border-indigo-500 bg-white cursor-pointer transition-all">
                            <div class="font-bold text-xs text-slate-900">${{r.action_name}}</div>
                            <div class="text-[11px] text-slate-500 mt-1">${{r.condition_description}}</div>
                            <div class="text-[10px] font-bold text-indigo-600 mt-2">Destino: ${{r.destination_state}} &rarr;</div>
                        </div>
                        `;
                    }});
                }}

                switchModalTab('m-tab-flow');
                document.getElementById('form-transition').classList.add('hidden');
                document.getElementById('modal-gestionar').classList.remove('hidden');
            }}

            function selectRule(caseId, actionName, reqAssignee, reqPercentage, reqDocs) {{
                document.getElementById('trans-case-id').value = caseId;
                document.getElementById('trans-action-name').value = actionName;
                document.getElementById('trans-title').innerText = "Acción Seleccionada: " + actionName;

                if (currentCaseData) {{
                    if (currentCaseData.claim_number && currentCaseData.claim_number !== "PENDIENTE") document.getElementById('m-inp-claim').value = currentCaseData.claim_number;
                    if (currentCaseData.origin_type) document.getElementById('m-inp-origin').value = currentCaseData.origin_type;
                    if (currentCaseData.event_type) document.getElementById('m-inp-event').value = currentCaseData.event_type;
                    if (currentCaseData.it_days !== null && currentCaseData.it_days !== undefined) document.getElementById('m-inp-it').value = currentCaseData.it_days;
                }}

                const fAssignee = document.getElementById('field-assignee');
                const fPercentage = document.getElementById('field-percentage');
                const fDocs = document.getElementById('field-docs');
                const lblAssignee = document.getElementById('lbl-assignee');
                const selAssignee = document.getElementById('sel-assignee');

                if (reqAssignee) {{
                    fAssignee.classList.remove('hidden');
                    if (actionName.includes("Dictaminar")) {{
                        lblAssignee.innerText = "Seleccionar Integrante de Comité Responsable *";
                        selAssignee.innerHTML = OPTIONS_COMITE;
                    }} else {{
                        lblAssignee.innerText = "Seleccionar Médico Calificador Responsable *";
                        selAssignee.innerHTML = OPTIONS_PCL;
                    }}
                }} else {{
                    fAssignee.classList.add('hidden');
                }}

                if (reqPercentage) fPercentage.classList.remove('hidden'); else fPercentage.classList.add('hidden');
                if (reqDocs) fDocs.classList.remove('hidden'); else fDocs.classList.add('hidden');

                document.getElementById('form-transition').classList.remove('hidden');
            }}

            document.getElementById('form-transition').addEventListener('submit', async (e) => {{
                e.preventDefault();
                const formData = new FormData(e.target);
                const res = await fetch('/api/cases/transition', {{ method: 'POST', body: formData }});

                if (res.ok) {{
                    window.location.reload();
                }} else {{
                    const data = await res.json();
                    alert(data.detail || "Error al ejecutar la transición.");
                }}
            }});

            const formCase = document.getElementById('form-case');
            if (formCase) {{
                formCase.addEventListener('submit', async (e) => {{
                    e.preventDefault();
                    const formData = new FormData(e.target);
                    const res = await fetch('/api/cases/create', {{ method: 'POST', body: formData }});

                    if (res.ok) {{
                        window.location.reload();
                    }} else {{
                        const data = await res.json();
                        alert(data.detail || "Error al radicar el caso.");
                    }}
                }});
            }}

            const formUser = document.getElementById('form-user');
            if (formUser) {{
                formUser.addEventListener('submit', async (e) => {{
                    e.preventDefault();
                    const formData = new FormData(e.target);
                    const res = await fetch('/api/users/create', {{ method: 'POST', body: formData }});

                    if (res.ok) {{
                        alert('Usuario registrado exitosamente.');
                        window.location.reload();
                    }} else {{
                        const data = await res.json();
                        alert(data.detail || "Error al registrar el usuario.");
                    }}
                }});
            }}
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
