import asyncio
import json
import logging
import os
from typing import cast, Dict, List, Optional
import uuid

from fastapi import FastAPI, Request, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel

app = FastAPI()

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- MIDDLEWARE DE PERMISOS ---
# Middleware para Permissions-Policy y filtrado de /cron
class PermissionsPolicyMiddleware(BaseHTTPMiddleware):
	async def dispatch(self, request: Request, call_next):
		# Filtrar solicitudes al endpoint /cron
		if request.url.path == "/cron":
			user_agent = request.headers.get("User-Agent", "")
			if "cron-job.org" not in user_agent.lower():
				return {"status": "forbidden", "message": "Acceso denegado"}
		
		response = await call_next(request)
		response.headers['Permissions-Policy'] = 'display-capture=(self)'
		return response

# --- MIDDLEWARE DE CORS ---
"""
app.add_middleware(
	CORSMiddleware,
	allow_origins=[
		"https://ruletaamericana.up.railway.app",
		"http://localhost:3000",
	],
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)
"""

app.add_middleware(
	CORSMiddleware,
	allow_origins=["https://ruletaamericana.up.railway.app"],
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)

# Añadir el middleware de permisos
app.add_middleware(PermissionsPolicyMiddleware)

# Montar directorio estático
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- CONFIGURACIÓN Y ESTADO GLOBAL ---
SECRET_KEY = "tu_clave_secreta_super_dificil"
STREAMER_USERNAME = "mario"
streamer_ws: Optional[WebSocket] = None
streamer_is_active = False

# --- MODELOS PYDANTIC ---
class ConsecutiveData(BaseModel):
	consecutive_id: int

class DateTimeData(BaseModel):
	fecha_hora_str: str

# --- CONNECTION MANAGER ---
class ConnectionManager:
	def __init__(self):
		self.active_connections: Dict[str, dict] = {}  # Cambiar a dict para almacenar websocket y username
		self.streamer_id: Optional[str] = None
		self.current_consecutive_id: Optional[int] = None

	async def connect(self, websocket: WebSocket) -> str:
		await websocket.accept()
		client_id = str(uuid.uuid4())
		self.active_connections[client_id] = {"websocket": websocket, "username": None}	 # Inicializar con username None
		logger.info(f"Nuevo cliente conectado con ID: {client_id}")
		await self.broadcast_json({"type": "usuarios_conectados", "payload": len(self.active_connections)})
		if self.current_consecutive_id is not None:
			await self.send_personal_json(
				{"type": "juego_numero", "payload": self.current_consecutive_id},
				client_id
			)
		return client_id

	def disconnect(self, client_id: str):
		global streamer_ws
		global streamer_is_active
		if client_id in self.active_connections:
			username = self.active_connections[client_id]["username"]
			del self.active_connections[client_id]
			logger.info(f"Cliente {client_id} ({username}) desconectado.")
			# Notificar al streamer si un espectador se desconecta
			if client_id != self.streamer_id and self.streamer_id:
				loop = asyncio.get_event_loop()
				if loop.is_running():
					loop.create_task(self.send_personal_json(
						{"type": "viewer_disconnected", "viewer_id": username},	 # Usar username
						self.streamer_id
					))
		if client_id == self.streamer_id:
			logger.info("El streamer se ha desconectado.")
			streamer_ws = None
			streamer_is_active = False
			self.streamer_id = None
			loop = asyncio.get_event_loop()
			if loop.is_running():
				loop.create_task(self.broadcast_json({"type": "stream_ended"}))
		loop = asyncio.get_event_loop()
		if loop.is_running():
			loop.create_task(self.broadcast_json({"type": "usuarios_conectados", "payload": len(self.active_connections)}))

	async def broadcast_json(self, data: dict):
		message = json.dumps(data)
		for client_id, info in list(self.active_connections.items()):
			try:
				await info["websocket"].send_text(message)
			except Exception as e:
				logger.error(f"Error enviando mensaje a {client_id}: {str(e)}")
				self.disconnect(client_id)

	async def send_personal_json(self, data: dict, client_id: str):
		if client_id in self.active_connections:
			try:
				await self.active_connections[client_id]["websocket"].send_text(json.dumps(data))
			except Exception as e:
				logger.error(f"Error enviando mensaje personal a {client_id}: {str(e)}")
				self.disconnect(client_id)


manager = ConnectionManager()

# --- ENDPOINTS HTTP ---
@app.get("/old")
async def get_old_version():
	return FileResponse(os.path.join(os.path.dirname(__file__), "index3.html"))

@app.get("/")
async def get_new_version():
	return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))

@app.post("/stream_ended")
async def notify_stream_ended(x_secret_key: Optional[str] = Header(None)):
	if x_secret_key != SECRET_KEY:
		raise HTTPException(status_code=403, detail="Clave secreta inválida")
	if manager.streamer_id:
		manager.disconnect(manager.streamer_id)
	return {"status": "ok"}



# Reemplazar la función broadcast
async def broadcast(message):
	"""Enviar mensaje a todos los clientes conectados"""
	await manager.broadcast_json(message)

# Modificar el endpoint WebSocket
@app.websocket("/ws/users")
async def websocket_users(websocket: WebSocket):
	global streamer_is_active  # Declare global at the start of the function
	client_id = await manager.connect(websocket)
	try:
		while True:
			data_str = await websocket.receive_text()
			data = json.loads(data_str)
			msg_type = data.get("type")
			logger.info(f"Mensaje recibido de {client_id}: {data}")

			if msg_type == "ping":
				await websocket.send_text(json.dumps({"type": "pong"}))
				continue

			elif msg_type == "login":
				username = data.get("name")
				if not username:
					await websocket.send_text(json.dumps({
						"type": "error",
						"message": "Nombre de usuario requerido"
					}))
					logger.error(f"Cliente {client_id} intentó autenticarse sin nombre")
					return

				# Almacenar mapeo de client_id a username
				manager.clients[client_id] = {"websocket": websocket, "username": username}

				if username == STREAMER_USERNAME:  # "mario"
					manager.streamer_id = username	# Usar username en lugar de client_id
					global streamer_ws
					global streamer_is_active
					streamer_ws = websocket
					streamer_is_active = True
					await websocket.send_text(json.dumps({
						"type": "login_success",
						"role": "streamer",
						"streamer_id": username,  # Enviar username como streamer_id
						"client_id": username	   # Enviar username como client_id
					}))
					logger.info(f"Cliente {client_id} autenticado como streamer: {username}")
				else:
					response = {
						"type": "login_success",
						"role": "viewer",
						"client_id": username  # Enviar username como client_id
					}
					if manager.streamer_id:
						response["streamer_id"] = manager.streamer_id
					await websocket.send_text(json.dumps(response))
					logger.info(f"Cliente {client_id} autenticado como viewer: {username}")

					# Notificar al streamer que un nuevo viewer se ha conectado
					if manager.streamer_id and streamer_ws and streamer_is_active:
						await manager.send_personal_json({
							"type": "viewer_joined",
							"viewer_id": username  # Enviar username como viewer_id
						}, manager.streamer_id)
						logger.info(f"Notificado al streamer {manager.streamer_id} sobre nuevo viewer: {username}")
			
			elif msg_type in ["offer", "answer", "candidate"]:
				target_id = data.get("target_id")
				# Buscar client_id correspondiente al username (target_id)
				target_client_id = None
				for cid, info in manager.active_connections.items():
					if info["username"] == target_id:
						target_client_id = cid
						break
				if target_client_id:
					await manager.send_personal_json(data, target_client_id)
				else:
					logger.error(f"No se encontró cliente con username: {target_id}")

			elif msg_type == "start_stream":
				if client_id == manager.streamer_id:
					streamer_is_active = True
					await manager.broadcast_json({
						"type": "stream_started",
						"video_dimensions": data.get("video_dimensions", {})
					})
					logger.info("El streamer ha iniciado la transmisión.")

			elif msg_type == "stream_ended":
				if client_id == manager.streamer_id:
					manager.disconnect(client_id)

			elif msg_type == "game_event" and client_id == manager.streamer_id:
				await manager.broadcast_json(data)
				await websocket.send_text(json.dumps({"type": "ack", "event": data["content"]}))
				logger.info(f"Confirmación enviada para {data['content']} a {client_id}")

			elif msg_type == "fecha_hora":
				if client_id == manager.streamer_id:
					await manager.broadcast_json({
						"type": "fecha_hora",
						"payload": data.get("fecha_hora_str")
					})
					logger.info(f"Fecha y hora enviada por {client_id}: {data.get('fecha_hora_str')}")

			elif msg_type == "consecutivo_juego":
				if client_id == manager.streamer_id:
					manager.current_consecutive_id = data.get("consecutive_id")
					await manager.broadcast_json({
						"type": "juego_numero",
						"payload": data.get("consecutive_id")
					})
					logger.info(f"Consecutivo enviado por {client_id}: {data.get('consecutive_id')}")

			elif msg_type == "numero_caido":
				if client_id == manager.streamer_id:
					await manager.broadcast_json({
						"type": "numero_caido",
						"payload": data.get("numero")
					})
					logger.info(f"Número caído enviado por {client_id}: {data.get('numero')}")

			elif msg_type == "apuesta":
				await manager.broadcast_json(data)

	except WebSocketDisconnect as e:
		logger.info(f"Cliente {client_id} desconectado con código {e.code}")
		manager.disconnect(client_id)
	except (ValueError, json.JSONDecodeError) as e:
		logger.error(f"Error de formato de mensaje del cliente {client_id}: {str(e)}")
		manager.disconnect(client_id)
	except Exception as e:
		logger.error(f"Error inesperado en WebSocket para {client_id}: {str(e)}")
		manager.disconnect(client_id)
