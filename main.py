# main.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import cast, List
import json
import os

import logging

# ==================== CÓDIGO UNIFICADO PARA FILTRAR LOGS ====================

# 1. Definimos UNA SOLA clase de filtro, la más flexible que hemos creado.
class EndpointFilter(logging.Filter):
    """
    Filtra los logs de acceso de Uvicorn para rutas específicas.
    """
    def __init__(self, paths_to_filter: List[str], *args, **kwargs):
        """
        Inicializa el filtro con una lista de inicios de rutas que se deben silenciar.
        Ejemplo: ["/live.jpg", "/upload"]
        """
        super().__init__(*args, **kwargs)
        # Guardamos la lista de rutas a silenciar
        self._paths = paths_to_filter

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Devuelve False (bloquear) si la ruta del log comienza con alguna de las rutas
        en self._paths. De lo contrario, devuelve True (permitir).
        """
        # Verificamos que los argumentos del log de acceso sean válidos
        if len(record.args) >= 3:
            # Obtenemos el path de la petición, ej: "/live.jpg?t=123"
            path = cast(str, record.args[2])
            
            # Revisamos si la ruta de la petición comienza con alguna de las rutas a filtrar
            for p in self._paths:
                if path.startswith(p):
                    return False  # Bloquea este log si hay una coincidencia

        # Para cualquier otro log que no coincida, permite que pase.
        return True

# 2. Creamos UNA SOLA lista con TODAS las rutas que queremos silenciar.
paths_to_silence = [
    "/live.jpg",
    "/upload",
    "/fecha_hora/"  # Añadimos la ruta de fecha/hora aquí
]

# 3. Obtenemos el logger de acceso de Uvicorn.
uvicorn_access_logger = logging.getLogger("uvicorn.access")

# 4. Le añadimos UNA SOLA instancia de nuestro filtro, pasándole la lista completa.
uvicorn_access_logger.addFilter(EndpointFilter(paths_to_filter=paths_to_silence))


app = FastAPI()

# Reemplaza tu variable html con esta versión completa y corregida

# --- Modelos Pydantic (sin cambios) ---
class Event(BaseModel): content: str
class NumeroCaido(BaseModel): numero: int
class ConsecutiveData(BaseModel): consecutive_id: int
class DateTimeData(BaseModel): fecha_hora_str: str

# --- ConnectionManager (sin cambios) ---
class ConnectionManager:
    def __init__(self):
        self.user_connections: List[WebSocket] = []
        self.blender_connections: List[WebSocket] = []
        self.apuestas_usuarios = {}

    async def connect_user(self, websocket: WebSocket):
        await websocket.accept()
        self.user_connections.append(websocket)
        await self.broadcast_to_users("Usuarios conectados: {}".format(len(self.user_connections)))

    def disconnect_user(self, websocket: WebSocket):
        if websocket in self.user_connections: self.user_connections.remove(websocket)
        if websocket in self.apuestas_usuarios: del self.apuestas_usuarios[websocket]

    async def broadcast_to_users(self, message: str):
        for connection in self.user_connections:
            try: await connection.send_text(message)
            except: self.disconnect_user(connection)

    async def connect_blender(self, websocket: WebSocket):
        await websocket.accept()
        if not self.blender_connections: self.blender_connections.append(websocket)
        else: print("Advertencia: Se intentó conectar un segundo cliente de Blender. Conexión rechazada.")

    def disconnect_blender(self, websocket: WebSocket):
        if websocket in self.blender_connections: self.blender_connections.remove(websocket)

    async def send_to_blender(self, message: str):
        for connection in self.blender_connections:
            try: await connection.send_text(message)
            except: self.disconnect_blender(connection)
    
    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

manager = ConnectionManager()

# --- Endpoints HTTP ---
# ==================== VERSIÓN UNIFICADA DEL ENDPOINT RAÍZ ====================
@app.get("/")
async def read_root():
    """
    Sirve el archivo HTML principal. 
    Para cambiar el archivo a servir para pruebas, simplemente comenta
    la línea actual y descomenta la que desees usar.
    """
    
    # --- Archivos de prueba (descomenta el que quieras usar) ---
    # file_to_serve = "index.html"
    # file_to_serve = "webrtc_capture.html"
    # file_to_serve = "webrtc_capture2.html"
    # file_to_serve = "recorte.html"
    file_to_serve = "recorte2.html" # <-- Archivo activo actualmente

    # --- Lógica para servir el archivo ---
    
    # Construye la ruta completa al archivo de forma segura
    # os.path.dirname(__file__) obtiene la carpeta donde se encuentra este script (main.py)
    html_file_path = os.path.join(os.path.dirname(__file__), file_to_serve)
    
    # Comprueba si el archivo realmente existe antes de intentar servirlo
    if os.path.exists(html_file_path):
        return FileResponse(html_file_path)
    else:
        # Si el archivo no se encuentra, devuelve un error 404 claro.
        raise HTTPException(status_code=404, detail=f"Archivo '{file_to_serve}' no encontrado.")

# ==============================================================================


@app.post("/send/")
async def receive_event(event: Event):
    message = event.content
    print("Evento recibido del juego:", message)
    # Reenviar a todos los usuarios web conectados
    await manager.broadcast_to_users(message)
    return {"status": "evento enviado a usuarios"}


@app.post("/consecutivo_juego/")
async def recibir_consecutivo(data: ConsecutiveData):
    payload = {"type": "juego_numero", "payload": data.consecutive_id}
    await manager.broadcast_to_users(json.dumps(payload))
    return {"status": "ok", "mensaje": "Consecutivo {} enviado.".format(data.consecutive_id)}


@app.post("/fecha_hora/")
async def recibir_fecha_hora(data: DateTimeData):
    payload = {"type": "fecha_hora", "payload": data.fecha_hora_str}
    await manager.broadcast_to_users(json.dumps(payload))
    return {"status": "ok", "mensaje": "Fecha y hora enviada."}

@app.post("/numero_caido/")
async def numero_caido(evento: NumeroCaido):
    numero_ganador = evento.numero
    print("Número caído recibido:", numero_ganador)
    await manager.broadcast_to_users("Número caído: {}".format(numero_ganador))
    for ws, apuesta_info in list(manager.apuestas_usuarios.items()):
        if apuesta_info.get("apuesta") == numero_ganador:
            mensaje_ganador = "¡Felicidades {}, has ganado!".format(apuesta_info.get("nombre"))
            await manager.send_personal_message(mensaje_ganador, ws)
    return {"status": "mensaje enviado"}

# --- Endpoints WebSocket ---
@app.websocket("/ws/users")
async def websocket_users(websocket: WebSocket):
    await manager.connect_user(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                apuesta_data = json.loads(data)
                manager.apuestas_usuarios[websocket] = apuesta_data
                # Esta es la línea clave que envía la apuesta a Blender
                await manager.send_to_blender("apuesta:{}".format(data))
                await manager.send_personal_message("Confirmación: tu apuesta por el número {} fue recibida".format(apuesta_data.get('apuesta')), websocket)
            except (ValueError, json.JSONDecodeError):
                await manager.send_personal_message("Error: formato de apuesta inválido", websocket)
    except WebSocketDisconnect:
        manager.disconnect_user(websocket)
        await manager.broadcast_to_users("Usuarios conectados: {}".format(len(manager.user_connections)))

# <--- ENDPOINT RE-INCORPORADO Y NECESARIO ---
@app.websocket("/ws/blender")
async def websocket_blender(websocket: WebSocket):
    """
    Este endpoint mantiene una conexión abierta con el script de Blender
    para poder enviarle las apuestas que llegan desde los usuarios web.
    """
    await manager.connect_blender(websocket)
    print("--> Conexión WebSocket de Blender establecida.")
    try:
        # Mantenemos el canal abierto para que Blender reciba mensajes.
        # No esperamos que Blender nos envíe nada por este canal.
        while True:
            await websocket.receive_text() # Espera pasivamente
    except WebSocketDisconnect:
        print("--> Conexión WebSocket de Blender cerrada.")
        manager.disconnect_blender(websocket)

# Una variable global para almacenar la imagen más reciente en memoria
latest_image_bytes = None
SECRET_KEY = "tu_clave_secreta_super_dificil" # Debe ser la misma que en el script capturador

@app.post("/upload")
async def upload_image(request: Request, file: UploadFile = File(...)):
    global latest_image_bytes
    # Medida de seguridad simple
    if request.headers.get("x-secret-key") != SECRET_KEY:
        raise HTTPException(status_code=403, detail="Clave secreta inválida")
    
    latest_image_bytes = await file.read()
    return {"message": "Imagen recibida"}

@app.get("/live.jpg")
async def get_live_image():
    if latest_image_bytes is None:
        # Podrías devolver una imagen por defecto de "Offline"
        raise HTTPException(status_code=404, detail="No hay imagen disponible")
    
    # Devuelve los bytes de la imagen con el tipo de contenido correcto
    return Response(content=latest_image_bytes, media_type="image/jpeg")

# Endpoint para servir tu página HTML

