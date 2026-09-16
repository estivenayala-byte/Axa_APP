from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
import uvicorn
import io
import pandas as pd
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel
from enum import Enum

app = FastAPI(title="Sistema de Gestión PCL - Flujo Limpio")

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
    COORDINADOR_REGISTRO = "COORDINADOR_REGISTRO"
    MEDICO_CALIFICADOR = "MEDICO_CALIFICADOR"
    COMITE_MEDICO = "COMITE_MEDICO"
    GESTOR_NOTIFICACIONES = "GESTOR_NOTIFICACIONES"
    AUDITOR_SISTEMA = "AUDITOR_SISTEMA"

class PCLCase(BaseModel):
    id: str  # ID Consecutivo: 1, 2, 3...
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

    # CAMPOS DE TRAZABILIDAD
    fecha_asignacion_pcl: Optional[str] = None
    fecha_calificacion: Optional[str] = None
    accion_pcl: Optional[str] = None
    fecha_solicitud_documentos: Optional[str] = None
    fecha_asignacion_comite: Optional[str] = None
    fecha_visado: Optional[str] = None
    accion_comite: Optional[str] = None
    fecha_notificacion_axa: Optional[str] = None

class AuditEntry(BaseModel):
    id: str  # ID Evento: 1, 2, 3...
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

# MATRIZ ESTRICTA DE TRANSICIONES (TEXTOS LIMPIOS SIN TEXTO NODOS)
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
        assigned_to="Lic. Paula Andrea Gómez", assigned_role=UserRole.COORDINADOR_REGISTRO,
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
        user_email="estiven.ayala@codess.org.co", user_role="Arquitecto & Auditor Líder PCL",
        action="REGISTRAR_CASO", origin_state="REGISTRO", destination_state="REGISTRO",
        origin_sub_step="REGISTRADO", destination_sub_step="REGISTRADO",
        timestamp="2026-09-10 08:30:00", comments="Radicación inicial del caso en la plataforma.",
        assigned_to_info="Lic. Paula Andrea Gómez"
    )
]

# EXPORTACIÓN EXCEL DE ESTADOS
@app.get("/api/export-excel-estados")
def export_excel_estados():
    output = io.BytesIO()
    cases_data = [{
        "ID Caso": c.id,
        "Paciente": c.patient_name,
        "Tipo Doc": c.document_type,
        "N° Doc": c.patient_id,
        "# Siniestro": c.claim_number,
        "Origen": c.origin_type,
        "Evento": c.event_type,
        "Tipo Calificación": c.qualification_type,
        "Días IT": c.it_days,
        "Empresa": c.company_name,
        "NIT": c.company_id or "N/A",
        "Módulo Actual": c.module_state.value,
        "Sub-Paso": c.sub_step.value,
        "Responsable Asignado": c.assigned_to,
        "% PCL Dictamen": f"{c.pcl_percentage}%" if c.pcl_percentage is not None else "--",
        "Fecha Radicación AXA": c.axa_filing_date,
        "Fecha creacion App": c.created_at,
        "Fecha Asigancion PCL": c.fecha_asignacion_pcl or "N/A",
        "Fecha Calificacion": c.fecha_calificacion or "N/A",
        "Accion PCL": c.accion_pcl or "N/A",
        "Fecha solicitud de documentos": c.fecha_solicitud_documentos or "N/A",
        "Documentos solicitados": c.requested_documents or "N/A",
        "Fecha Asignacion Comité": c.fecha_asignacion_comite or "N/A",
        "Fecha Visado": c.fecha_visado or "N/A",
        "Accion Comité": c.accion_comite or "N/A",
        "Fecha Notificacion AXA": c.fecha_notificacion_axa or "N/A",
        "Ultima Modificacion": c.updated_at
    } for c in CASES_DB]

    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.DataFrame(cases_data).to_excel(writer, sheet_name='Estados_Casos_PCL', index=False)

    output.seek(0)
    filename = f"Casos_PCL_Estados_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
    return StreamingResponse(
        output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.get("/api/export-excel-auditoria")
def export_excel_auditoria():
    output = io.BytesIO()
    audit_data = [{
        "ID Evento": a.id,
        "ID Caso": a.case_id,
        "Fecha Exacta": a.timestamp,
        "Usuario Responsable": a.user_name,
        "Email": a.user_email,
        "Rol": a.user_role,
        "Acción Realizada": a.action,
        "Asignado A": a.assigned_to_info or "N/A",
        "Módulo Origen": a.origin_state,
        "Sub-Paso Origen": a.origin_sub_step,
        "Módulo Destino": a.destination_state,
        "Sub-Paso Destino": a.destination_sub_step,
        "Observaciones / Motivo": a.comments
    } for a in AUDIT_DB]

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
    case = next((c for c in CASES_DB if c.id == case_id), None)
    if not case:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    
    rules = [r for r in STATE_TRANSITIONS_MATRIX if r["current_state"] == case.module_state and r["current_sub_step"] == case.sub_step]
    audits = [a for a in AUDIT_DB if a.case_id == case_id]
    return {"case": case.dict(), "rules": rules, "audits": [a.dict() for a in audits]}

# API EJECUTAR TRANSICIÓN
@app.post("/api/cases/transition")
def transition_case(
    case_id: str = Form(...), action_name: str = Form(...), comments: str = Form(...),
    new_assignee: Optional[str] = Form(None), pcl_percentage: Optional[float] = Form(None),
    requested_docs: Optional[str] = Form(None)
):
    case = next((c for c in CASES_DB if c.id == case_id), None)
    if not case:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")

    rule = next((r for r in STATE_TRANSITIONS_MATRIX if r["action_name"] == action_name and r["current_state"] == case.module_state), None)
    if not rule:
        raise HTTPException(status_code=400, detail="Transición no permitida según la matriz de estados.")

    if rule.get("requires_reason") and not comments.strip():
        raise HTTPException(status_code=400, detail="Es obligatorio ingresar las observaciones de auditoría.")

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
    elif rule["destination_state"] == ModuleState.REGISTRO:
        case.assigned_to = "Lic. Paula Andrea Gómez"

    if rule["destination_state"] == ModuleState.CALIFICACION_PCL:
        case.fecha_asignacion_pcl = now_str

    if rule["destination_state"] == ModuleState.COMITE:
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

    event_id = str(len(AUDIT_DB) + 1)

    audit = AuditEntry(
        id=event_id, case_id=case_id,
        user_name="Estiven Ayala", user_email="estiven.ayala@codess.org.co", user_role="Auditor Líder PCL",
        action=action_name, origin_state=origin_state, destination_state=case.module_state.value,
        origin_sub_step=origin_sub_step, destination_sub_step=case.sub_step.value,
        timestamp=now_str, comments=comments, assigned_to_info=assigned_info
    )
    AUDIT_DB.insert(0, audit)
    return {"success": True, "case": case.dict()}

# RADICAR CASO NUEVO
@app.post("/api/cases/create")
def create_case(
    document_type: str = Form(...), patient_id: str = Form(...), patient_name: str = Form(...),
    claim_number: str = Form(...), origin_type: str = Form(...), event_type: str = Form(...),
    qualification_type: str = Form(...), it_days: int = Form(...), company_name: str = Form(...),
    company_id: Optional[str] = Form(None), axa_filing_date: str = Form(...),
    assigned_doctor: Optional[str] = Form(None), notes: Optional[str] = Form(None)
):
    if not patient_id.isdigit():
        raise HTTPException(status_code=400, detail="El Número de Documento debe contener únicamente números (0-9).")
    if not claim_number.isdigit():
        raise HTTPException(status_code=400, detail="El # de Siniestro debe contener únicamente números (0-9).")
    if not axa_filing_date.strip():
        raise HTTPException(status_code=400, detail="La Fecha de Radicación AXA es obligatoria.")

    case_id = str(len(CASES_DB) + 1)
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    doc_assigned = assigned_doctor or "Dra. Marcela Restrepo"

    new_case = PCLCase(
        id=case_id, document_type=document_type, patient_id=patient_id, patient_name=patient_name.upper(),
        claim_number=claim_number, origin_type=origin_type, event_type=event_type,
        qualification_type=qualification_type, it_days=it_days, company_name=company_name.upper(),
        company_id=company_id, axa_filing_date=axa_filing_date, module_state=ModuleState.CALIFICACION_PCL,
        sub_step=SubStep.ASIGNADO, assigned_to=doc_assigned, assigned_role=UserRole.MEDICO_CALIFICADOR,
        created_by="estiven.ayala@codess.org.co", created_at=now_str, updated_at=now_str,
        notes=notes, pcl_percentage=None, fecha_asignacion_pcl=now_str
    )

    event_id = str(len(AUDIT_DB) + 1)

    audit = AuditEntry(
        id=event_id, case_id=case_id, user_name="Estiven Ayala",
        user_email="estiven.ayala@codess.org.co", user_role="Auditor Líder PCL",
        action="REGISTRAR_Y_ASIGNAR_CASO", origin_state="REGISTRO", destination_state="CALIFICACION_PCL",
        origin_sub_step="REGISTRADO", destination_sub_step="ASIGNADO", timestamp=now_str,
        comments=f"Creación formal e ingreso directo a Calificación PCL. Asignado a: {doc_assigned}.",
        assigned_to_info=doc_assigned
    )

    CASES_DB.insert(0, new_case)
    AUDIT_DB.insert(0, audit)
    return {"success": True, "id": case_id}

# FRONTEND
@app.get("/", response_class=HTMLResponse)
def serve_ui():
    en_tramite = len([c for c in CASES_DB if c.module_state not in [ModuleState.CIERRE_ADMINISTRATIVO, ModuleState.GESTIONADO]])
    finalizados = len(CASES_DB) - en_tramite

    def render_cases_cards(state_filter: Optional[List[ModuleState]] = None):
        filtered = [c for c in CASES_DB if c.module_state in state_filter] if state_filter else CASES_DB
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
                        <span class="text-[10px] font-bold px-2 py-0.5 rounded border bg-sky-50 text-sky-700 border-sky-200">{c.module_state.value}</span>
                    </div>
                    <h4 class="text-sm font-bold text-slate-900 uppercase">{c.patient_name}</h4>
                    <div class="text-xs text-slate-500 font-mono mt-0.5">{c.document_type}: <strong>{c.patient_id}</strong> &bull; Sin. <strong class="text-indigo-700">#{c.claim_number}</strong></div>
                    <div class="flex items-center gap-1.5 mt-2 flex-wrap">
                        <span class="text-[10px] font-bold px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 border">{c.qualification_type}</span>
                        <span class="text-[10px] font-mono font-bold px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-700 border">{c.event_type} - {c.origin_type}</span>
                        <span class="text-[10px] font-mono px-1.5 py-0.5 rounded bg-amber-50 text-amber-800 border">{c.it_days} Días IT</span>
                        <span class="text-[10px] font-bold px-1.5 py-0.5 rounded bg-violet-50 text-violet-800 border">PCL: {pcl_val}</span>
                    </div>
                    <div class="text-xs text-slate-600 flex items-center gap-1.5 mt-2">🏢 <span class="truncate">{c.company_name}</span></div>
                </div>
                <div class="pt-3 mt-3 border-t border-slate-100 flex items-center justify-between text-[11px] text-slate-500">
                    <span>👤 {c.assigned_to}</span>
                    <button onclick="openManageModal('{c.id}')" class="text-indigo-600 font-bold hover:underline">Gestionar &rarr;</button>
                </div>
            </div>
            """
        return cards

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
                                <span class="text-xs font-semibold px-2 py-0.5 rounded-md bg-indigo-50 text-indigo-700 border border-indigo-200">5 Módulos</span>
                            </div>
                            <p class="text-xs text-slate-500 font-medium">Peritación de Pérdida de Capacidad Laboral & Auditoría Integral</p>
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
                        <button onclick="document.getElementById('modal-nuevo').classList.remove('hidden')" class="px-3.5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold shadow-xs">+ Nuevo Caso</button>
                        
                        <a href="/api/export-excel-estados" class="px-3 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold shadow-xs">📄 Excel Estados</a>
                        <a href="/api/export-excel-auditoria" class="px-3 py-2 rounded-lg bg-indigo-800 hover:bg-indigo-900 text-white text-xs font-semibold shadow-xs">📜 Excel Auditoría</a>
                    </div>
                </div>
                
                <div class="mt-3 pt-2.5 border-t border-slate-100 flex items-center justify-between gap-2 overflow-x-auto">
                    <div class="flex items-center space-x-1.5">
                        <button onclick="switchTab('mod-nuevos')" id="btn-mod-nuevos" class="tab-btn px-3 py-2 rounded-xl text-xs font-bold bg-slate-900 text-white shadow-xs whitespace-nowrap">✨ Casos Nuevos</button>
                        <button onclick="switchTab('mod-admin')" id="btn-mod-admin" class="tab-btn px-3 py-2 rounded-xl text-xs font-bold bg-white text-slate-700 border border-slate-200 whitespace-nowrap">📁 Gestión Admin</button>
                        <button onclick="switchTab('mod-calificacion')" id="btn-mod-calificacion" class="tab-btn px-3 py-2 rounded-xl text-xs font-bold bg-white text-slate-700 border border-slate-200 whitespace-nowrap">🩺 Calificación PCL</button>
                        <button onclick="switchTab('mod-comite')" id="btn-mod-comite" class="tab-btn px-3 py-2 rounded-xl text-xs font-bold bg-white text-slate-700 border border-slate-200 whitespace-nowrap">👥 Comité</button>
                        <button onclick="switchTab('mod-cierre')" id="btn-mod-cierre" class="tab-btn px-3 py-2 rounded-xl text-xs font-bold bg-white text-slate-700 border border-slate-200 whitespace-nowrap">📤 Pendiente Cierre</button>
                    </div>
                    <div class="flex items-center space-x-1 pl-2 border-l border-slate-200">
                        <button onclick="switchTab('mod-finalizados')" id="btn-mod-finalizados" class="tab-btn px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-white text-slate-600 whitespace-nowrap">📦 Finalizados</button>
                        <button onclick="switchTab('mod-auditoria')" id="btn-mod-auditoria" class="tab-btn px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-white text-slate-600 whitespace-nowrap">📜 Auditoría General</button>
                    </div>
                </div>
            </div>
        </header>

        <main class="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6">
            <div id="mod-nuevos" class="tab-content space-y-4">
                <div class="bg-gradient-to-br from-indigo-50 via-white to-indigo-50/40 p-5 rounded-2xl border border-indigo-100 flex items-center justify-between">
                    <div>
                        <h3 class="text-sm font-bold text-slate-900">✨ Ventanilla Única de Radicación e Ingreso Pericial</h3>
                        <p class="text-xs text-slate-600 mt-1">Ingreso automático de casos con ID consecutivo numérico simple (1, 2, 3...).</p>
                    </div>
                    <button onclick="document.getElementById('modal-nuevo').classList.remove('hidden')" class="px-4 py-2 rounded-xl bg-indigo-600 text-white text-xs font-semibold">Abrir Formulario de Ingreso</button>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">{render_cases_cards([ModuleState.REGISTRO])}</div>
            </div>

            <div id="mod-admin" class="tab-content hidden space-y-4">
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">{render_cases_cards([ModuleState.REGISTRO, ModuleState.EN_SOLICITUD_DOCUMENTOS])}</div>
            </div>

            <div id="mod-calificacion" class="tab-content hidden space-y-4">
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">{render_cases_cards([ModuleState.CALIFICACION_PCL])}</div>
            </div>

            <div id="mod-comite" class="tab-content hidden space-y-4">
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">{render_cases_cards([ModuleState.COMITE])}</div>
            </div>

            <div id="mod-cierre" class="tab-content hidden space-y-4">
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">{render_cases_cards([ModuleState.PENDIENTE_CIERRE])}</div>
            </div>

            <div id="mod-finalizados" class="tab-content hidden space-y-4">
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">{render_cases_cards([ModuleState.CIERRE_ADMINISTRATIVO, ModuleState.GESTIONADO])}</div>
            </div>

            <div id="mod-auditoria" class="tab-content hidden space-y-4">
                <div class="bg-white rounded-xl border overflow-hidden shadow-xs">
                    <table class="w-full text-left text-xs text-slate-700">
                        <thead class="bg-slate-50 text-[11px] uppercase font-bold border-b">
                            <tr><th class="py-3 px-4">ID Evento</th><th class="py-3 px-4">ID Caso</th><th class="py-3 px-4">Fecha</th><th class="py-3 px-4">Usuario</th><th class="py-3 px-4">Acción</th><th class="py-3 px-4">Transición</th><th class="py-3 px-4">Observaciones</th></tr>
                        </thead>
                        <tbody>
                            {"".join([f'<tr class="border-b"><td class="py-3 px-4 font-mono font-bold text-slate-500">{a.id}</td><td class="py-3 px-4 font-mono font-bold text-indigo-700">{a.case_id}</td><td class="py-3 px-4">{a.timestamp}</td><td class="py-3 px-4">{a.user_name}</td><td class="py-3 px-4 font-semibold">{a.action}</td><td class="py-3 px-4"><span class="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-100">{a.origin_state} &rarr; {a.destination_state}</span></td><td class="py-3 px-4 text-slate-600">{a.comments}</td></tr>' for a in AUDIT_DB])}
                        </tbody>
                    </table>
                </div>
            </div>
        </main>

        <!-- MODAL FORMULARIO INGRESO -->
        <div id="modal-nuevo" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-xs hidden">
            <div class="bg-white rounded-2xl shadow-2xl border border-slate-200 w-full max-w-3xl overflow-hidden">
                <div class="px-6 py-4 bg-slate-900 text-white flex items-center justify-between">
                    <h2 class="text-base font-bold">Radicar Nuevo Caso de Peritación PCL</h2>
                    <button onclick="document.getElementById('modal-nuevo').classList.add('hidden')" class="text-slate-400 hover:text-white">&times;</button>
                </div>
                
                <form id="form-case" class="p-6 space-y-4 max-h-[80vh] overflow-y-auto">
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
                                <input type="text" name="patient_name" required oninput="this.value = this.value.toUpperCase()" placeholder="EJ: CARLOS ALBERTO RESTREPO GÓMEZ" class="w-full px-3 py-2 text-xs uppercase font-semibold rounded-lg border border-slate-300">
                            </div>
                        </div>
                    </div>

                    <div class="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-3">
                        <h3 class="text-xs font-bold uppercase text-slate-800 border-b pb-1">2. Parámetros Técnicos de Peritación PCL</h3>
                        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
                            <div>
                                <label class="block text-xs font-bold text-slate-700 mb-1"># Siniestro (Solo 0-9) *</label>
                                <input type="text" name="claim_number" required onkeypress="return event.charCode >= 48 && event.charCode <= 57" placeholder="Ej: 8920194" class="w-full px-3 py-2 text-xs font-mono rounded-lg border border-slate-300">
                            </div>
                            <div>
                                <label class="block text-xs font-bold text-slate-700 mb-1">Tipo de Origen *</label>
                                <select name="origin_type" class="w-full px-3 py-2 text-xs rounded-lg border border-slate-300"><option>Laboral</option><option>Común</option><option>Mixto</option></select>
                            </div>
                            <div>
                                <label class="block text-xs font-bold text-slate-700 mb-1">Tipo de Evento *</label>
                                <select name="event_type" class="w-full px-3 py-2 text-xs rounded-lg border border-slate-300"><option>AT</option><option>EL</option></select>
                            </div>
                            <div>
                                <label class="block text-xs font-bold text-slate-700 mb-1">Tipo de Calificación *</label>
                                <select name="qualification_type" class="w-full px-3 py-2 text-xs rounded-lg border border-slate-300">
                                    <option>ATEL</option><option>COMBO</option><option>REVISION</option><option>AMEEC</option><option>COMBO AST</option><option>COMUN</option><option>DTO</option><option>NORMAL</option><option>TUTELA</option><option>INTEGRAL</option>
                                </select>
                            </div>
                            <div>
                                <label class="block text-xs font-bold text-slate-700 mb-1">Días IT *</label>
                                <input type="number" name="it_days" value="180" min="0" class="w-full px-3 py-2 text-xs rounded-lg border border-slate-300">
                            </div>
                            <div>
                                <label class="block text-xs font-bold text-slate-700 mb-1">Fecha Radicación AXA *</label>
                                <input type="date" name="axa_filing_date" required class="w-full px-3 py-2 text-xs rounded-lg border border-slate-300 bg-white">
                            </div>
                        </div>
                    </div>

                    <div class="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-3">
                        <h3 class="text-xs font-bold uppercase text-slate-800 border-b pb-1">3. Vinculación Laboral y Perito Responsable</h3>
                        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                            <div>
                                <label class="block text-xs font-bold text-slate-700 mb-1">Empresa / Empleador *</label>
                                <input type="text" name="company_name" required oninput="this.value = this.value.toUpperCase()" class="w-full px-3 py-2 text-xs uppercase rounded-lg border border-slate-300">
                            </div>
                            <div>
                                <label class="block text-xs font-bold text-slate-700 mb-1">NIT / ID Empresa</label>
                                <input type="text" name="company_id" placeholder="Ej: 900.284.195-2" class="w-full px-3 py-2 text-xs font-mono rounded-lg border border-slate-300">
                            </div>
                            <div class="sm:col-span-2">
                                <label class="block text-xs font-bold text-slate-700 mb-1">Médico Calificador Asignado *</label>
                                <select name="assigned_doctor" class="w-full px-3 py-2 text-xs rounded-lg border border-slate-300">
                                    <option>Dra. Marcela Restrepo (Médico Calificador Especialista)</option>
                                    <option>Dr. Carlos Fernando Mora (Comité Médico)</option>
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

        <!-- MODAL DINÁMICO DE GESTIÓN -->
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

                            <div class="font-bold text-xs text-indigo-900" id="trans-title">Confirmar Transición</div>
                            
                            <div id="field-assignee" class="hidden">
                                <label class="block text-xs font-bold text-slate-800 mb-1" id="lbl-assignee">Seleccionar Integrante Responsable *</label>
                                <select id="sel-assignee" name="new_assignee" class="w-full px-3 py-2 text-xs rounded-lg border bg-white font-semibold text-slate-800">
                                    <optgroup label="Integrantes del Comité Médico">
                                        <option>Dr. Carlos Fernando Mora (Presidente Comité Médico)</option>
                                    </optgroup>
                                    <optgroup label="Médicos Calificadores">
                                        <option>Dra. Marcela Restrepo (Médico Calificador Especialista)</option>
                                    </optgroup>
                                    <optgroup label="Gestión y Registro">
                                        <option>Lic. Paula Andrea Gómez (Coordinadora de Registro)</option>
                                        <option>Ing. Javier Hernán Torres (Oficial de Notificaciones)</option>
                                    </optgroup>
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
                                <div><strong>Empresa:</strong> <span id="d-company"></span></div>
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

            async function openManageModal(caseId) {{
                const res = await fetch('/api/cases/' + caseId);
                if (!res.ok) return;

                const data = await res.json();
                const c = data.case;
                const rules = data.rules;
                const audits = data.audits;

                document.getElementById('m-case-id').innerText = "ID Caso: " + c.id + " (" + c.module_state + " - " + c.sub_step + ")";
                document.getElementById('m-patient-name').innerText = c.patient_name + " | C.C. " + c.patient_id + " | " + c.company_name;

                document.getElementById('d-name').innerText = c.patient_name;
                document.getElementById('d-doc').innerText = c.document_type + " " + c.patient_id;
                document.getElementById('d-claim').innerText = "#" + c.claim_number;
                document.getElementById('d-company').innerText = c.company_name + (c.company_id ? " (NIT: " + c.company_id + ")" : "");
                document.getElementById('d-insurer').innerText = c.axa_filing_date ? c.axa_filing_date : "N/A";
                document.getElementById('d-origin').innerText = c.origin_type + " - " + c.event_type + " (" + c.qualification_type + ")";
                document.getElementById('d-it').innerText = c.it_days + " Días";
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

                const fAssignee = document.getElementById('field-assignee');
                const fPercentage = document.getElementById('field-percentage');
                const fDocs = document.getElementById('field-docs');
                const lblAssignee = document.getElementById('lbl-assignee');
                const selAssignee = document.getElementById('sel-assignee');

                if (reqAssignee) {{
                    fAssignee.classList.remove('hidden');
                    if (actionName.includes("Dictaminar")) {{
                        lblAssignee.innerText = "Seleccionar Integrante de Comité Responsable *";
                    }} else {{
                        lblAssignee.innerText = "Seleccionar Médico Calificador Responsable *";
                    }}
                }} else {{
                    fAssignee.classList.add('hidden');
                    selAssignee.value = "";
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

            document.getElementById('form-case').addEventListener('submit', async (e) => {{
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
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)