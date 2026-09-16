from fastapi import FastAPI, Request, Form, HTTPException, Depends, status
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
import uvicorn
import io
import pandas as pd
from datetime import datetime
from typing import Optional, List, Dict
from pydantic import BaseModel
from enum import Enum

app = FastAPI(title="Sistema de Gestión PCL - Control de Acceso y Roles")

# =============================================================
# MODELOS DE DATOS Y ENUMS
# =============================================================

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

# USUARIOS Y PERMISOS DEL SISTEMA
USERS_DB: Dict[str, dict] = {
    "admin": {
        "username": "admin",
        "name": "Estiven Ayala (Administrador Sistema)",
        "password": "admin123*",
        "role": UserRole.ADMINISTRADOR,
        "email": "estiven.ayala@codess.org.co"
    },
    "calificador1": {
        "username": "calificador1",
        "name": "Dra. Marcela Restrepo",
        "password": "calificador123*",
        "role": UserRole.MEDICO_CALIFICADOR,
        "email": "marcela.restrepo@pcl.com"
    },
    "calificador2": {
        "username": "calificador2",
        "name": "Dr. Alejandro Gómez",
        "password": "calificador123*",
        "role": UserRole.MEDICO_CALIFICADOR,
        "email": "alejandro.gomez@pcl.com"
    },
    "comite1": {
        "username": "comite1",
        "name": "Dr. Carlos Fernando Mora",
        "password": "comite123*",
        "role": UserRole.MEDICO_COMITE,
        "email": "carlos.mora@pcl.com"
    }
}

class PCLCase(BaseModel):
    id: str
    document_type: str
    patient_id: str
    patient_name: str
    claim_number: str
    origin_type: str
    event_type: str
    qualification_type: str
    it_days: int
    company_name: str
    company_id: Optional[str] = None
    axa_filing_date: str
    insurer: Optional[str] = "AXA Colpatria Seguros"
    module_state: ModuleState
    sub_step: SubStep
    assigned_to: str
    assigned_role: UserRole
    created_by: str
    created_at: str
    updated_at: str
    priority: str = "MEDIA"
    pcl_percentage: Optional[float] = None
    deficit_details: Optional[str] = None
    requested_documents: Optional[str] = None
    closing_reason: Optional[str] = None
    notes: Optional[str] = None

    fecha_asignacion_pcl: Optional[str] = None
    fecha_calificacion: Optional[str] = None
    accion_pcl: Optional[str] = None
    fecha_solicitud_documentos: Optional[str] = None
    fecha_asignacion_comite: Optional[str] = None
    fecha_visado: Optional[str] = None
    accion_comite: Optional[str] = None
    fecha_notificacion_axa: Optional[str] = None

class AuditEntry(BaseModel):
    id: str
    case_id: str
    user_name: str
    user_email: str
    user_role: str
    action: str
    origin_state: str
    destination_state: str
    origin_sub_step: str
    destination_sub_step: str
    timestamp: str
    comments: str
    assigned_to_info: Optional[str] = None

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

# BASE DE DATOS INICIAL
CASES_DB: List[PCLCase] = [
    PCLCase(
        id="1", document_type="Cédula de Ciudadanía", patient_id="1020485921",
        patient_name="ANDRÉS FELIPE MORALES CASTRO", claim_number="8920194", origin_type="Laboral",
        event_type="AT", qualification_type="ATEL", it_days=180, company_name="MANUFACTURAS ANDINAS S.A.S.",
        company_id="900.284.195-2", axa_filing_date="2026-09-09", insurer="AXA Colpatria Seguros",
        module_state=ModuleState.REGISTRO, sub_step=SubStep.REGISTRADO,
        assigned_to="Lic. Paula Andrea Gómez", assigned_role=UserRole.ADMINISTRADOR,
        created_by="estiven.ayala@codess.org.co", created_at="2026-09-10 08:30:00", updated_at="2026-09-10 08:30:00",
        pcl_percentage=None, notes="Expediente radicado con folios iniciales."
    ),
    PCLCase(
        id="2", document_type="Cédula de Ciudadanía", patient_id="52893412",
        patient_name="GLORIA ESPERANZA RINCÓN ORTIZ", claim_number="7829103", origin_type="Laboral",
        event_type="AT", qualification_type="COMBO", it_days=240, company_name="TRANSPORTES Y LOGÍSTICA EXPRESS",
        company_id="860.012.784-1", axa_filing_date="2026-09-07", insurer="Seguros Bolívar ARL",
        module_state=ModuleState.CALIFICACION_PCL, sub_step=SubStep.ASIGNADO,
        assigned_to="Dra. Marcela Restrepo", assigned_role=UserRole.MEDICO_CALIFICADOR,
        created_by="paula.gomez@pcl-registro.co", created_at="2026-09-08 10:15:00", updated_at="2026-09-09 14:20:00",
        fecha_asignacion_pcl="2026-09-09 14:20:00", pcl_percentage=None
    )
]

AUDIT_DB: List[AuditEntry] = [
    AuditEntry(
        id="1", case_id="1", user_name="Estiven Ayala",
        user_email="estiven.ayala@codess.org.co", user_role="ADMINISTRADOR",
        action="REGISTRAR_CASO", origin_state="REGISTRO", destination_state="REGISTRO",
        origin_sub_step="REGISTRADO", destination_sub_step="REGISTRADO",
        timestamp="2026-09-10 08:30:00", comments="Radicación inicial del caso en la plataforma.",
        assigned_to_info="Lic. Paula Andrea Gómez"
    )
]

# EXPORTACIÓN EXCEL
@app.get("/api/export-excel-estados")
def export_excel_estados():
    output = io.BytesIO()
    cases_data = [{
        "ID Caso": c.id, "Paciente": c.patient_name, "Tipo Doc": c.document_type,
        "N° Doc": c.patient_id, "# Siniestro": c.claim_number, "Origen": c.origin_type,
        "Evento": c.event_type, "Tipo Calificación": c.qualification_type, "Días IT": c.it_days,
        "Empresa": c.company_name, "NIT": c.company_id or "N/A", "Módulo Actual": c.module_state.value,
        "Sub-Paso": c.sub_step.value, "Responsable Asignado": c.assigned_to,
        "% PCL Dictamen": f"{c.pcl_percentage}%" if c.pcl_percentage is not None else "--",
        "Fecha Radicación AXA": c.axa_filing_date, "Fecha creacion App": c.created_at,
        "Fecha Asigancion PCL": c.fecha_asignacion_pcl or "N/A", "Fecha Calificacion": c.fecha_calificacion or "N/A",
        "Accion PCL": c.accion_pcl or "N/A", "Fecha solicitud de documentos": c.fecha_solicitud_documentos or "N/A",
        "Documentos solicitados": c.requested_documents or "N/A", "Fecha Asignacion Comité": c.fecha_asignacion_comite or "N/A",
        "Fecha Visado": c.fecha_visado or "N/A", "Accion Comité": c.accion_comite or "N/A",
        "Fecha Notificacion AXA": c.fecha_notificacion_axa or "N/A", "Ultima Modificacion": c.updated_at
    } for c in CASES_DB]

    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.DataFrame(cases_data).to_excel(writer, sheet_name='Estados_Casos_PCL', index=False)

    output.seek(0)
    filename = f"Casos_PCL_Estados_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
    return StreamingResponse(
        output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.get("/api/cases/{case_id}")
def get_case_detail(case_id: str):
    case = next((c for c in CASES_DB if c.id == case_id), None)
    if not case:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    
    rules = [r for r in STATE_TRANSITIONS_MATRIX if r["current_state"] == case.module_state and r["current_sub_step"] == case.sub_step]
    audits = [a for a in AUDIT_DB if a.case_id == case_id]
    return {"case": case.dict(), "rules": rules, "audits": [a.dict() for a in audits]}

@app.post("/api/cases/transition")
def transition_case(
    case_id: str = Form(...), action_name: str = Form(...), comments: str = Form(...),
    new_assignee: Optional[str] = Form(None), pcl_percentage: Optional[float] = Form(None),
    requested_docs: Optional[str] = Form(None), current_user: str = Form("admin")
):
    case = next((c for c in CASES_DB if c.id == case_id), None)
    if not case:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")

    rule = next((r for r in STATE_TRANSITIONS_MATRIX if r["action_name"] == action_name and r["current_state"] == case.module_state), None)
    if not rule:
        raise HTTPException(status_code=400, detail="Transición no permitida según la matriz de estados.")

    user_info = USERS_DB.get(current_user, USERS_DB["admin"])

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    origin_state = case.module_state.value
    origin_sub_step = case.sub_step.value

    case.module_state = rule["destination_state"]
    case.sub_step = rule["destination_sub_step"]
    case.updated_at = now_str

    assigned_info = None
    if rule.get("requires_assignee") and new_assignee and new_assignee.strip():
        case.assigned_to = new_assignee
        assigned_info = new_assignee

    if rule["destination_state"] == ModuleState.CALIFICACION_PCL:
        case.fecha_asignacion_pcl = now_str

    if rule["destination_state"] == ModuleState.COMITE:
        case.fecha_asignacion_comite = now_str

    if action_name in ["Dictaminar y Calificar Caso", "Ajustar y Recalificar (Re-evaluación)", "Devolución Administrativa"]:
        case.fecha_calificacion = now_str
        case.accion_pcl = action_name
        if rule.get("requires_percentage") and pcl_percentage is not None:
            case.pcl_percentage = pcl_percentage

    elif action_name in ["Aprobar Visado de Comité", "Devolución a Calificador"]:
        case.fecha_visado = now_str
        case.accion_comite = action_name

    event_id = str(len(AUDIT_DB) + 1)
    audit = AuditEntry(
        id=event_id, case_id=case_id,
        user_name=user_info["name"], user_email=user_info["email"], user_role=user_info["role"].value,
        action=action_name, origin_state=origin_state, destination_state=case.module_state.value,
        origin_sub_step=origin_sub_step, destination_sub_step=case.sub_step.value,
        timestamp=now_str, comments=comments, assigned_to_info=assigned_info
    )
    AUDIT_DB.insert(0, audit)
    return {"success": True, "case": case.dict()}

@app.post("/api/cases/create")
def create_case(
    document_type: str = Form(...), patient_id: str = Form(...), patient_name: str = Form(...),
    claim_number: str = Form(...), origin_type: str = Form(...), event_type: str = Form(...),
    qualification_type: str = Form(...), it_days: int = Form(...), company_name: str = Form(...),
    company_id: Optional[str] = Form(None), axa_filing_date: str = Form(...),
    assigned_doctor: Optional[str] = Form(None), current_user: str = Form("admin")
):
    user_info = USERS_DB.get(current_user, USERS_DB["admin"])
    case_id = str(len(CASES_DB) + 1)
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    doc_assigned = assigned_doctor or "Dra. Marcela Restrepo"

    new_case = PCLCase(
        id=case_id, document_type=document_type, patient_id=patient_id, patient_name=patient_name.upper(),
        claim_number=claim_number, origin_type=origin_type, event_type=event_type,
        qualification_type=qualification_type, it_days=it_days, company_name=company_name.upper(),
        company_id=company_id, axa_filing_date=axa_filing_date, module_state=ModuleState.CALIFICACION_PCL,
        sub_step=SubStep.ASIGNADO, assigned_to=doc_assigned, assigned_role=UserRole.MEDICO_CALIFICADOR,
        created_by=user_info["email"], created_at=now_str, updated_at=now_str,
        fecha_asignacion_pcl=now_str
    )

    event_id = str(len(AUDIT_DB) + 1)
    audit = AuditEntry(
        id=event_id, case_id=case_id, user_name=user_info["name"],
        user_email=user_info["email"], user_role=user_info["role"].value,
        action="REGISTRAR_Y_ASIGNAR_CASO", origin_state="REGISTRO", destination_state="CALIFICACION_PCL",
        origin_sub_step="REGISTRADO", destination_sub_step="ASIGNADO", timestamp=now_str,
        comments=f"Creación e ingreso directo a Calificación PCL. Asignado a: {doc_assigned}.",
        assigned_to_info=doc_assigned
    )

    CASES_DB.insert(0, new_case)
    AUDIT_DB.insert(0, audit)
    return {"success": True, "id": case_id}

# VISTA Y AUTENTICACIÓN
@app.get("/", response_class=HTMLResponse)
def serve_ui(user: Optional[str] = None):
    if not user or user not in USERS_DB:
        # PANTALLA DE LOGIN
        return """
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <title>Inicio de Sesión - Sistema PCL</title>
            <script src="https://cdn.tailwindcss.com"></script>
        </head>
        <body class="bg-slate-900 flex items-center justify-center h-screen">
            <div class="bg-white p-8 rounded-2xl shadow-2xl w-full max-w-md">
                <div class="text-center mb-6">
                    <div class="w-12 h-12 bg-indigo-600 rounded-xl mx-auto flex items-center justify-center text-white text-xl font-bold mb-2">⚡</div>
                    <h2 class="text-xl font-bold text-slate-800">Sistema de Gestión PCL</h2>
                    <p class="text-xs text-slate-500">Inicie sesión para acceder a su módulo asignado</p>
                </div>
                <form action="/" method="get" class="space-y-4">
                    <div>
                        <label class="block text-xs font-bold text-slate-700 mb-1">Seleccionar Usuario / Perfil</label>
                        <select name="user" class="w-full px-3 py-2 text-xs rounded-lg border border-slate-300 font-semibold">
                            <option value="admin">🔑 Estiven Ayala (Administrador - Acceso Total)</option>
                            <option value="calificador1">🩺 Dra. Marcela Restrepo (Médico Calificador PCL)</option>
                            <option value="calificador2">🩺 Dr. Alejandro Gómez (Médico Calificador PCL)</option>
                            <option value="comite1">👥 Dr. Carlos Fernando Mora (Médico Comité)</option>
                        </select>
                    </div>
                    <button type="submit" class="w-full py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-xs rounded-lg shadow-md transition-all">Ingresar al Sistema &rarr;</button>
                </form>
            </div>
        </body>
        </html>
        """

    current_u = USERS_DB[user]
    role = current_u["role"]

    # RESTRICCIÓN DE MODULOS POR ROL
    if role == UserRole.MEDICO_CALIFICADOR:
        allowed_states = [ModuleState.CALIFICACION_PCL]
    elif role == UserRole.MEDICO_COMITE:
        allowed_states = [ModuleState.COMITE]
    else:
        allowed_states = list(ModuleState)

    def render_cases_cards(state_filter: List[ModuleState]):
        filtered = [c for c in CASES_DB if c.module_state in state_filter]
        if not filtered:
            return '<div class="col-span-3 text-center py-8 text-xs text-slate-500 bg-white rounded-xl border border-slate-200">No hay expedientes activos en esta bandeja.</div>'
        
        cards = ""
        for c in filtered:
            pcl_val = f"{c.pcl_percentage}%" if c.pcl_percentage is not None else "--"
            cards += f"""
            <div class="bg-white rounded-xl border border-slate-200 p-4 shadow-xs hover:shadow-md transition-all flex flex-col justify-between">
                <div>
                    <div class="flex items-center justify-between gap-2 mb-2">
                        <span class="font-mono font-bold text-sm text-indigo-700">ID Caso: {c.id}</span>
                        <span class="text-[10px] font-bold px-2 py-0.5 rounded border bg-sky-50 text-sky-700 border-sky-200">{c.module_state.value}</span>
                    </div>
                    <h4 class="text-sm font-bold text-slate-900 uppercase">{c.patient_name}</h4>
                    <div class="text-xs text-slate-500 font-mono mt-0.5">{c.document_type}: <strong>{c.patient_id}</strong> &bull; Sin. <strong class="text-indigo-700">#{c.claim_number}</strong></div>
                    <div class="flex items-center gap-1.5 mt-2 flex-wrap">
                        <span class="text-[10px] font-bold px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 border">{c.qualification_type}</span>
                        <span class="text-[10px] font-mono px-1.5 py-0.5 rounded bg-violet-50 text-violet-800 border">PCL: {pcl_val}</span>
                    </div>
                </div>
                <div class="pt-3 mt-3 border-t border-slate-100 flex items-center justify-between text-[11px] text-slate-500">
                    <span>👤 {c.assigned_to}</span>
                    <button onclick="openManageModal('{c.id}')" class="text-indigo-600 font-bold hover:underline">Gestionar &rarr;</button>
                </div>
            </div>
            """
        return cards

    # RENDERIZAR VISTA SEGÚN PERMISOS
    show_calificacion = ModuleState.CALIFICACION_PCL in allowed_states
    show_comite = ModuleState.COMITE in allowed_states
    show_admin = role == UserRole.ADMINISTRADOR

    return f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>Sistema PCL - {current_u['name']}</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
        <style> body {{ font-family: 'Inter', sans-serif; }} </style>
    </head>
    <body class="bg-slate-100 text-slate-900 min-h-screen flex flex-col">
        <header class="bg-white border-b border-slate-200 sticky top-0 z-30 shadow-xs">
            <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-3 flex justify-between items-center">
                <div class="flex items-center space-x-3">
                    <div class="w-10 h-10 rounded-xl bg-indigo-600 flex items-center justify-center text-white font-bold">⚡</div>
                    <div>
                        <h1 class="text-lg font-bold text-slate-900">Sistema de Gestión PCL</h1>
                        <p class="text-xs text-slate-500">Perfil Activo: <strong class="text-indigo-700">{current_u['name']}</strong> ({role.value})</p>
                    </div>
                </div>
                <div class="flex items-center gap-3">
                    {f'<button onclick="document.getElementById(\'modal-nuevo\').classList.remove(\'hidden\')" class="px-3.5 py-2 rounded-lg bg-indigo-600 text-white text-xs font-semibold">+ Nuevo Caso</button>' if show_admin else ''}
                    <a href="/api/export-excel-estados" class="px-3 py-2 rounded-lg bg-emerald-600 text-white text-xs font-semibold">📄 Excel Estados</a>
                    <a href="/" class="px-3 py-2 rounded-lg bg-slate-200 hover:bg-slate-300 text-slate-800 text-xs font-semibold">🚪 Cerrar Sesión</a>
                </div>
            </div>
            
            <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-2 border-t flex gap-2 overflow-x-auto text-xs font-bold">
                {f'<button onclick="switchTab(\'mod-calificacion\')" id="btn-mod-calificacion" class="tab-btn px-3 py-1.5 rounded-lg bg-indigo-600 text-white">🩺 Calificación PCL</button>' if show_calificacion else ''}
                {f'<button onclick="switchTab(\'mod-comite\')" id="btn-mod-comite" class="tab-btn px-3 py-1.5 rounded-lg bg-white border text-slate-700">👥 Comité Médico</button>' if show_comite else ''}
                {f'<button onclick="switchTab(\'mod-admin\')" id="btn-mod-admin" class="tab-btn px-3 py-1.5 rounded-lg bg-white border text-slate-700">📁 Administración General</button>' if show_admin else ''}
            </div>
        </header>

        <main class="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6">
            {f'<div id="mod-calificacion" class="tab-content"><h3 class="text-xs font-bold uppercase text-slate-600 mb-3">Módulo Calificación PCL</h3><div class="grid grid-cols-1 md:grid-cols-3 gap-4">{render_cases_cards([ModuleState.CALIFICACION_PCL])}</div></div>' if show_calificacion else ''}
            {f'<div id="mod-comite" class="tab-content {"hidden" if show_calificacion else ""}"><h3 class="text-xs font-bold uppercase text-slate-600 mb-3">Módulo Comité Médico</h3><div class="grid grid-cols-1 md:grid-cols-3 gap-4">{render_cases_cards([ModuleState.COMITE])}</div></div>' if show_comite else ''}
            {f'<div id="mod-admin" class="tab-content hidden"><h3 class="text-xs font-bold uppercase text-slate-600 mb-3">Bandeja Global de Casos</h3><div class="grid grid-cols-1 md:grid-cols-3 gap-4">{render_cases_cards(list(ModuleState))}</div></div>' if show_admin else ''}
        </main>

        <!-- MODAL INGRESO -->
        <div id="modal-nuevo" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 hidden">
            <div class="bg-white rounded-2xl shadow-2xl border w-full max-w-2xl p-6 space-y-4">
                <h3 class="text-sm font-bold border-b pb-2">Radicar Nuevo Caso PCL</h3>
                <form id="form-case" class="space-y-3 text-xs">
                    <input type="hidden" name="current_user" value="{user}">
                    <div class="grid grid-cols-2 gap-3">
                        <div><label class="block font-bold">Tipo Doc *</label><select name="document_type" class="w-full border p-2 rounded"><option>Cédula de Ciudadanía</option><option>Cédula de Extranjería</option></select></div>
                        <div><label class="block font-bold">Documento *</label><input type="text" name="patient_id" required class="w-full border p-2 rounded"></div>
                        <div class="col-span-2"><label class="block font-bold">Nombre Completo *</label><input type="text" name="patient_name" required oninput="this.value=this.value.toUpperCase()" class="w-full border p-2 rounded uppercase"></div>
                        <div><label class="block font-bold"># Siniestro *</label><input type="text" name="claim_number" required class="w-full border p-2 rounded"></div>
                        <div><label class="block font-bold">Fecha AXA *</label><input type="date" name="axa_filing_date" required class="w-full border p-2 rounded"></div>
                        <div><label class="block font-bold">Origen *</label><select name="origin_type" class="w-full border p-2 rounded"><option>Laboral</option><option>Común</option></select></div>
                        <div><label class="block font-bold">Evento *</label><select name="event_type" class="w-full border p-2 rounded"><option>AT</option><option>EL</option></select></div>
                        <div><label class="block font-bold">Tipo Calificación *</label><select name="qualification_type" class="w-full border p-2 rounded"><option>ATEL</option><option>COMBO</option><option>NORMAL</option></select></div>
                        <div><label class="block font-bold">Días IT *</label><input type="number" name="it_days" value="180" class="w-full border p-2 rounded"></div>
                        <div class="col-span-2"><label class="block font-bold">Empresa *</label><input type="text" name="company_name" required oninput="this.value=this.value.toUpperCase()" class="w-full border p-2 rounded uppercase"></div>
                        <div class="col-span-2"><label class="block font-bold">Médico Calificador Asignado *</label>
                            <select name="assigned_doctor" class="w-full border p-2 rounded font-semibold">
                                <option>Dra. Marcela Restrepo</option>
                                <option>Dr. Alejandro Gómez</option>
                            </select>
                        </div>
                    </div>
                    <div class="flex justify-end gap-2 pt-3 border-t">
                        <button type="button" onclick="document.getElementById('modal-nuevo').classList.add('hidden')" class="px-3 py-1.5 border rounded">Cancelar</button>
                        <button type="submit" class="px-4 py-1.5 bg-indigo-600 text-white font-bold rounded">Radicar Caso</button>
                    </div>
                </form>
            </div>
        </div>

        <!-- MODAL GESTIÓN -->
        <div id="modal-gestionar" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 hidden">
            <div class="bg-white rounded-2xl shadow-2xl border w-full max-w-2xl p-6 space-y-4">
                <div class="flex justify-between border-b pb-2">
                    <h3 id="m-case-id" class="text-sm font-bold text-indigo-700">Gestionar Caso</h3>
                    <button onclick="document.getElementById('modal-gestionar').classList.add('hidden')" class="font-bold">&times;</button>
                </div>
                <div id="m-rules-list" class="grid grid-cols-1 gap-2"></div>

                <form id="form-transition" class="hidden p-3 border rounded bg-slate-50 space-y-3 text-xs">
                    <input type="hidden" id="trans-case-id" name="case_id">
                    <input type="hidden" id="trans-action-name" name="action_name">
                    <input type="hidden" name="current_user" value="{user}">

                    <div id="field-assignee" class="hidden">
                        <label class="block font-bold mb-1">Médico Responsable *</label>
                        <select name="new_assignee" class="w-full border p-2 rounded font-semibold">
                            <option>Dr. Carlos Fernando Mora</option>
                            <option>Dra. Marcela Restrepo</option>
                            <option>Dr. Alejandro Gómez</option>
                        </select>
                    </div>

                    <div id="field-percentage" class="hidden">
                        <label class="block font-bold mb-1">% PCL Dictaminado *</label>
                        <input type="number" step="0.01" name="pcl_percentage" value="25.00" class="w-32 border p-2 rounded font-bold">
                    </div>

                    <div>
                        <label class="block font-bold mb-1">Observaciones / Auditoría *</label>
                        <textarea name="comments" required rows="2" class="w-full border p-2 rounded" placeholder="Ingrese las notas del dictamen..."></textarea>
                    </div>

                    <div class="flex justify-end gap-2">
                        <button type="button" onclick="document.getElementById('form-transition').classList.add('hidden')" class="px-3 py-1 border rounded">Cancelar</button>
                        <button type="submit" class="px-4 py-1 bg-indigo-600 text-white font-bold rounded">Guardar y Procesar</button>
                    </div>
                </form>
            </div>
        </div>

        <script>
            function switchTab(tabId) {{
                document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
                document.querySelectorAll('.tab-btn').forEach(btn => {{
                    btn.classList.remove('bg-indigo-600', 'text-white');
                    btn.classList.add('bg-white', 'text-slate-700', 'border');
                }});
                document.getElementById(tabId).classList.remove('hidden');
                const activeBtn = document.getElementById('btn-' + tabId);
                if (activeBtn) {{
                    activeBtn.classList.remove('bg-white', 'text-slate-700', 'border');
                    activeBtn.classList.add('bg-indigo-600', 'text-white');
                }}
            }}

            async function openManageModal(caseId) {{
                const res = await fetch('/api/cases/' + caseId);
                if (!res.ok) return;
                const data = await res.json();
                const rules = data.rules;

                document.getElementById('m-case-id').innerText = "Gestión de Expediente ID: " + caseId;
                const rulesList = document.getElementById('m-rules-list');
                rulesList.innerHTML = "";

                if (rules.length === 0) {{
                    rulesList.innerHTML = '<div class="text-xs text-slate-500 italic p-2 border bg-slate-50 rounded">Este expediente se encuentra en un estado final sin transiciones salientes.</div>';
                }} else {{
                    rules.forEach(r => {{
                        const reqAssignee = r.requires_assignee ? 'true' : 'false';
                        const reqPercentage = r.requires_percentage ? 'true' : 'false';
                        rulesList.innerHTML += `
                        <div onclick="selectRule('${{caseId}}', '${{r.action_name}}', ${{reqAssignee}}, ${{reqPercentage}})" class="p-3 rounded border hover:border-indigo-600 cursor-pointer bg-white transition-all">
                            <div class="font-bold text-xs text-slate-800">${{r.action_name}}</div>
                            <div class="text-[11px] text-slate-500">${{r.condition_description}}</div>
                        </div>
                        `;
                    }});
                }}
                document.getElementById('form-transition').classList.add('hidden');
                document.getElementById('modal-gestionar').classList.remove('hidden');
            }}

            function selectRule(caseId, actionName, reqAssignee, reqPercentage) {{
                document.getElementById('trans-case-id').value = caseId;
                document.getElementById('trans-action-name').value = actionName;

                const fAssignee = document.getElementById('field-assignee');
                const fPercentage = document.getElementById('field-percentage');

                if (reqAssignee) fAssignee.classList.remove('hidden'); else fAssignee.classList.add('hidden');
                if (reqPercentage) fPercentage.classList.remove('hidden'); else fPercentage.classList.add('hidden');

                document.getElementById('form-transition').classList.remove('hidden');
            }}

            document.getElementById('form-transition').addEventListener('submit', async (e) => {{
                e.preventDefault();
                const formData = new FormData(e.target);
                const res = await fetch('/api/cases/transition', {{ method: 'POST', body: formData }});
                if (res.ok) window.location.reload();
            }});

            const formCase = document.getElementById('form-case');
            if (formCase) {{
                formCase.addEventListener('submit', async (e) => {{
                    e.preventDefault();
                    const formData = new FormData(e.target);
                    const res = await fetch('/api/cases/create', {{ method: 'POST', body: formData }});
                    if (res.ok) window.location.reload();
                }});
            }}
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
