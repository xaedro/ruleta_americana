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
class PermissionsPolicyMiddleware(BaseHTTPMiddleware):
	async def dispatch(self, request: Request, call_next):
		if request.url.path == "/cron":
			user_agent = request.headers.get("User-Agent", "")
			if "cron-job.org" not in user_agent.lower():
				return {"status": "forbidden", "message": "Acceso denegado"}
		
		response = await call_next(request)
		response.headers['Permissions-Policy'] = 'display-capture=(self)'
		return response

# --- MIDDLEWARE DE CORS ---
app.add_middleware(
	CORSMiddleware,
	allow_origins=["https://ruletaamericana.up.railway.app"],
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)

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
		self.active_connections: Dict[str, dict] = {}
		self.streamer_id: Optional[str] = None
		self.current_consecutive_id: Optional[int] = None
		# NUEVO: Para guardar las dimensiones del video actual
		self.stream_dimensions: dict = {} 
	async def connect(self, websocket: WebSocket) -> str:
		await websocket.accept()
		client_id = str(uuid.uuid4())
		self.active_connections[client_id] = {"websocket": websocket, "username": None}
		logger.info(f"Nuevo cliente conectado con ID: {client_id}, active_connections={len(self.active_connections)}")
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
			logger.info(f"Cliente {client_id} ({username}) desconectado, active_connections={len(self.active_connections)}")
			logger.debug(f"active_connections: {[(cid, info['username']) for cid, info in self.active_connections.items()]}")
			if client_id != self.streamer_id and self.streamer_id and username:
				loop = asyncio.get_event_loop()
				if loop.is_running():
					loop.create_task(self.send_personal_json(
						{"type": "viewer_disconnected", "viewer_id": username},
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
			loop.create_task(self.broadcast_json({"type": "usuarios_conectados"}))
	
	async def broadcast_json(self, data: dict):
		message = data.copy()
		if data.get("type") == "usuarios_conectados":
			valid_connections = [
				info for info in self.active_connections.values()
				if info["username"] is not None
			]
			num_connections = len(valid_connections)
			message["payload"] = num_connections
			usernames = [info["username"] for info in valid_connections]
			message["usernames"] = usernames[:10] if num_connections >= 11 else usernames
			logger.info(f"Enviando usuarios_conectados: payload={num_connections}, usernames={usernames}")
			logger.debug(f"active_connections: {[(cid, info['username']) for cid, info in self.active_connections.items()]}")
		
		message_str = json.dumps(message)
		for client_id, info in list(self.active_connections.items()):
			try:
				await info["websocket"].send_text(message_str)
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

async def broadcast(message):
	await manager.broadcast_json(message)

@app.websocket("/ws/users")
async def websocket_users(websocket: WebSocket):
	global streamer_is_active
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
					continue
				
				# Bandera de control de flujo para evitar la autenticación
				abort_login = False
				
				# 1. Verificar si el username ya está en uso por OTRA conexión
				existing_client_id = None
				for cid, info in manager.active_connections.items():
					if info["username"] == username and cid != client_id: 
						existing_client_id = cid
						break
				
				if existing_client_id:
					# Duplicado encontrado, intentar ping/limpieza
					try:
						# Verificar si la conexión existente está activa (Ping/Pong)
						await manager.active_connections[existing_client_id]["websocket"].send_text(json.dumps({"type": "ping"}))
						async with asyncio.timeout(5):
							message = await manager.active_connections[existing_client_id]["websocket"].receive_text()
							data_received = json.loads(message)
							
							if data_received.get("type") != "pong":
								logger.warning(f"Cliente {existing_client_id} envió mensaje inesperado en lugar de pong: {data_received}")
								manager.disconnect(existing_client_id)
							else:
								# Conexión activa, enviar error al nuevo cliente
								await websocket.send_text(json.dumps({
									"type": "error",
									"message": f"Ya estás logueado con el nombre '{username}'"
								}))
								logger.error(f"Cliente {client_id} intentó usar nombre duplicado: {username}")
								abort_login = True
								
					except asyncio.TimeoutError:
						logger.error(f"Cliente {existing_client_id} no respondió al ping, desconectando")
						manager.disconnect(existing_client_id)
					except Exception as e:
						logger.error(f"Error en ping para {existing_client_id}: {str(e)}")
						manager.disconnect(existing_client_id)
					
					# 2. Re-verificar (por si la limpieza falló o el cliente activo no respondió al ping)
					is_duplicate_still_active = False
					for cid, info in manager.active_connections.items():
						if info["username"] == username and cid != client_id:
							is_duplicate_still_active = True
							break
					
					if is_duplicate_still_active:
						await websocket.send_text(json.dumps({
							"type": "error",
							"message": f"Ya estás logueado con el nombre '{username}'"
						}))
						logger.error(f"Cliente {client_id} intentó usar nombre duplicado tras limpieza: {username}")
						abort_login = True
				
				# --- FIN DE LA LÓGICA DE DUPLICADOS ---
				
				# Si la bandera abort_login es True (se envió un error), salta al siguiente ciclo del while True.
				if abort_login:
					continue 
				
				# Continúa la autenticación (Asignar username)
				if client_id in manager.active_connections:
					manager.active_connections[client_id]["username"] = username
				else:
					logger.error(f"Cliente {client_id} no encontrado en active_connections")
					continue
					
				# Lógica de asignación de rol de Streamer
				if username == STREAMER_USERNAME:
					if manager.streamer_id:
						await websocket.send_text(json.dumps({
							"type": "error",
							"message": "Ya hay un streamer conectado con el nombre 'mario'"
						}))
						logger.error(f"Cliente {client_id} intentó autenticarse como streamer, pero ya existe: {manager.streamer_id}")
						continue
										
					manager.streamer_id = client_id	
					global streamer_ws
					global streamer_is_active
					streamer_ws = websocket
					streamer_is_active = True
					await websocket.send_text(json.dumps({
						"type": "login_success",
						"role": "streamer",
						"streamer_id": username, 
						"client_id": username 
					}))
					logger.info(f"Cliente {client_id} autenticado como streamer: {username}")
				# Lógica de asignación de rol de Viewer
				else:
					response = {
						"type": "login_success",
						"role": "viewer",
						"client_id": username
					}
					if manager.streamer_id:
						response["streamer_id"] = manager.streamer_id # OJO: Aquí debe ser manager.streamer_id (el UUID) o el username según tu lógica corregida anterior.
						# Si corregiste el login como te dije antes, manager.streamer_id es el UUID.
						# Pero el cliente espera el ID para WebRTC. Asegúrate que coincida.
						# Para simplificar y no romper nada:
						response["streamer_id"] = manager.streamer_id 

					await websocket.send_text(json.dumps(response))
					logger.info(f"Cliente {client_id} autenticado como viewer: {username}")
					
					if manager.streamer_id and streamer_ws and streamer_is_active:
						await manager.send_personal_json({
							"type": "viewer_joined",
							"viewer_id": username
						}, manager.streamer_id)
						logger.info(f"Notificado al streamer {manager.streamer_id} sobre nuevo viewer: {username}")

						# --- NUEVO BLOQUE: SI EL STREAM YA ESTÁ ACTIVO, AVISAR AL NUEVO USUARIO ---
						logger.info(f"Stream activo detectado. Enviando stream_started a {client_id}")
						await manager.send_personal_json({
							"type": "stream_started",
							"video_dimensions": manager.stream_dimensions
						}, client_id)
				await manager.broadcast_json({"type": "usuarios_conectados"})
			elif msg_type == "pong":
				logger.debug(f"Recibido pong de {client_id}")
				continue

			elif msg_type in ["offer", "answer", "candidate"]:
				target_id = data.get("target_id")
				target_client_id = None
				for cid, info in manager.active_connections.items():
					if info["username"] == target_id:
						target_client_id = cid
						break
				if target_client_id:
					await manager.send_personal_json(data, target_client_id)
					logger.info(f"Mensaje {msg_type} enviado de {data.get('from_id')} a {target_id}")
				else:
					logger.error(f"No se encontró cliente con username: {target_id}")

			elif msg_type == "start_stream":
				if client_id == manager.streamer_id:
					streamer_is_active = True
					
					# NUEVO: Guardar las dimensiones en el manager
					manager.stream_dimensions = data.get("video_dimensions", {})
					
					await manager.broadcast_json({
						"type": "stream_started",
						"video_dimensions": data.get("video_dimensions", {})
					})
					logger.info("El streamer ha iniciado la transmisión.")

			elif msg_type == "stream_ended":
				if client_id == manager.streamer_id:
					manager.disconnect(client_id)

			elif msg_type == "game_event"  and data.get("source") == "blender":
				await manager.broadcast_json(data)
				await websocket.send_text(json.dumps({"type": "ack", "event": data["content"]}))
				logger.info(f"Confirmación enviada para {data['content']} a {client_id}")

			elif msg_type == "fecha_hora":
				await manager.broadcast_json({
					"type": "fecha_hora",
					"payload": data.get("fecha_hora_str")
				})
				logger.info(f"Fecha y hora enviada por {client_id}: {data.get('fecha_hora_str')}")

			elif msg_type == "consecutivo_juego" and data.get("source") == "blender":
				manager.current_consecutive_id = data.get("consecutive_id")
				await manager.broadcast_json({
					"type": "juego_numero",
					"payload": data.get("consecutive_id")
				})
				logger.info(f"Consecutivo enviado por {client_id}: {data.get('consecutive_id')}")

			elif msg_type == "numero_caido" and data.get("source") == "blender":
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





























