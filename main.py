# main.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import List
import json
import os

import logging
from typing import cast

# 1. Creamos la clase de filtro mejorada
class EndpointFilter(logging.Filter):
    def __init__(self, path: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Guardamos la ruta que queremos filtrar
        self._path = path

    def filter(self, record: logging.LogRecord) -> bool:
        # Los argumentos del log de acceso de uvicorn son una tupla.
        # El tercer elemento (índice 2) es la ruta de la petición (ej: "/fecha_hora/").
        # Hacemos una comprobación segura para evitar errores de índice.
        if len(record.args) >= 3:
            # `cast` es solo para ayudar al autocompletado del editor, no es funcionalmente necesario.
            path = cast(str, record.args[2])
            if self._path in path:
                # Si la ruta que queremos filtrar está en la ruta de la petición,
                # devolvemos False para bloquear el log.
                return False
        return True

# 2. Obtenemos el logger de acceso y le añadimos una instancia de nuestro filtro.
# Le decimos que filtre cualquier log que contenga "/fecha_hora/".
logging.getLogger("uvicorn.access").addFilter(EndpointFilter(path="/fecha_hora/"))



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
@app.get("/")
async def get_index():
    """
    Sirve el archivo index.html como respuesta a la petición raíz.
    """
    # Define la ruta al archivo HTML.
    # Usar os.path.join es una buena práctica para que funcione en cualquier sistema operativo.
    html_file_path = os.path.join(os.path.dirname(__file__), "index.html")
    
    # Comprueba si el archivo existe para evitar errores 500 si se elimina por accidente.
    if os.path.exists(html_file_path):
        return FileResponse(html_file_path)
    else:
        return {"error": "index.html not found"}, 404
async def get(): return HTMLResponse(html)


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