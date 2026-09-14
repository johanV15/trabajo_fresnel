# Despliegue en VPS (Debian 13)

Pasos exactos, en orden, para desplegar la aplicación en un VPS Debian 13
limpio (probado sobre el tipo Hostinger KVM 2 mencionado en la
especificación, pero aplica a cualquier VPS Debian 13). Reemplaza:

- `TU_DOMINIO` por el dominio real (ej. `fresnel.midominio.com`), que debe
  tener ya un registro DNS tipo A apuntando a la IP del VPS **antes** del
  paso de certbot.
- `TU_USUARIO_SSH` por tu usuario de acceso SSH con privilegios sudo.

No se ejecuta nada de esto automáticamente: son los comandos, a mano, en
este orden.

---

## 0. Antes de empezar

- El registro DNS de `TU_DOMINIO` debe apuntar a la IP del VPS (verifica
  con `dig TU_DOMINIO +short` desde tu máquina local).
- Necesitas acceso SSH con un usuario con privilegios `sudo`.
- El repositorio del proyecto debe estar accesible (Git, o el propio
  directorio `fresnel/` copiado por `scp`/`rsync`).

---

## 1. Actualizar el sistema e instalar paquetes base

Conectado por SSH al VPS:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git nginx ufw
```

Debian 13 trae Python 3.12+ de fábrica, suficiente (el proyecto requiere
3.9+).

---

## 2. Crear un usuario de sistema dedicado

La aplicación no debe correr como `root` ni como tu usuario de login.

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin fresnel
```

---

## 3. Llevar el código al servidor

Opción A — clonar desde tu repositorio Git (recomendado, facilita
actualizaciones futuras):

```bash
sudo git clone <URL_DE_TU_REPOSITORIO> /opt/fresnel
```

Opción B — copiar el directorio `fresnel/` local por `rsync` (si no usas
Git remoto):

```bash
# Desde tu máquina local, no desde el VPS:
rsync -avz --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache' \
  ./fresnel/ TU_USUARIO_SSH@TU_DOMINIO:/tmp/fresnel-deploy/
ssh TU_USUARIO_SSH@TU_DOMINIO "sudo mv /tmp/fresnel-deploy /opt/fresnel"
```

Y en cualquier caso, ya en el VPS:

```bash
sudo chown -R fresnel:fresnel /opt/fresnel
```

`cache/` está en `.gitignore` (y `git` no versiona directorios vacíos), así
que después de clonar probablemente **no exista todavía**. Créala ahora:
la unidad systemd (paso 6) usa `ReadWritePaths=/opt/fresnel/cache`, que
necesita que la carpeta ya exista para poder arrancar.

```bash
sudo -u fresnel mkdir -p /opt/fresnel/cache
```

---

## 4. Entorno virtual y dependencias (versiones fijadas)

```bash
sudo -u fresnel python3 -m venv /opt/fresnel/.venv
sudo -u fresnel /opt/fresnel/.venv/bin/pip install --upgrade pip
sudo -u fresnel /opt/fresnel/.venv/bin/pip install -r /opt/fresnel/requirements.txt
```

`requirements.txt` ya trae las versiones fijadas (las 6 dependencias
directas del proyecto — `fastapi`, `uvicorn`, `pyproj`, `numpy`, `httpx`,
`pytest` — más sus dependencias transitivas, también fijadas para que la
instalación en el servidor sea idéntica a la probada en desarrollo).

---

## 5. Verificación manual antes de systemd

Antes de automatizar nada, confirma que arranca:

```bash
sudo -u fresnel /opt/fresnel/.venv/bin/uvicorn api.main:app \
  --app-dir /opt/fresnel --host 127.0.0.1 --port 8000
```

En otra terminal (o con `curl` desde el mismo VPS):

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/docs
```

Debe responder `200`. Detén el proceso (`Ctrl+C`) antes de seguir.

---

## 6. Unidad systemd

Copia la unidad ya preparada (`deploy/fresnel.service` de este repo) al
lugar correcto:

```bash
sudo cp /opt/fresnel/deploy/fresnel.service /etc/systemd/system/fresnel.service
sudo systemctl daemon-reload
sudo systemctl enable --now fresnel.service
```

Verifica:

```bash
sudo systemctl status fresnel.service
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/docs
```

Logs en vivo si algo falla:

```bash
sudo journalctl -u fresnel.service -f
```

---

## 7. nginx como proxy inverso

Copia la configuración ya preparada (`deploy/nginx-fresnel.conf` de este
repo), **reemplazando `TU_DOMINIO` por el dominio real**:

```bash
sudo cp /opt/fresnel/deploy/nginx-fresnel.conf /etc/nginx/sites-available/fresnel
sudo sed -i 's/TU_DOMINIO/tu-dominio-real.com/' /etc/nginx/sites-available/fresnel
sudo ln -s /etc/nginx/sites-available/fresnel /etc/nginx/sites-enabled/fresnel
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

Verifica en HTTP (todavía sin certificado) desde tu máquina local:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://TU_DOMINIO/docs
```

---

## 8. Firewall (ufw)

**Orden crítico:** permite SSH *antes* de activar el firewall, o te
quedas fuera del servidor.

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status verbose
```

`Nginx Full` abre tanto el puerto 80 (HTTP, necesario para la validación
de certbot) como el 443 (HTTPS). El puerto 8000 (uvicorn) **no** se abre
al exterior: solo escucha en `127.0.0.1`, nginx es el único punto de
entrada.

---

## 9. HTTPS con certbot

Con el DNS ya apuntando al VPS y nginx respondiendo en HTTP (paso 7):

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d TU_DOMINIO
```

Certbot edita `/etc/nginx/sites-available/fresnel` automáticamente
(agrega el bloque `listen 443 ssl`, la redirección 80→443, y las rutas a
los certificados) y recarga nginx. Sigue el prompt interactivo (pide un
correo de contacto y confirma la redirección automática a HTTPS —
conviene aceptarla).

Verifica:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://TU_DOMINIO/docs
```

Renovación automática: certbot instala su propio timer de systemd
(`certbot.timer`), no requiere cron manual. Verifica que existe:

```bash
systemctl list-timers | grep certbot
```

---

## 10. Verificación final

```bash
curl -s https://TU_DOMINIO/api/demos | python3 -m json.tool
```

Debe listar los 3 casos demo. Abre `https://TU_DOMINIO/` en el navegador
y confirma que carga el caso "despejado" automáticamente.

---

## Actualizar un despliegue existente

```bash
cd /opt/fresnel
sudo -u fresnel git pull
sudo -u fresnel /opt/fresnel/.venv/bin/pip install -r requirements.txt
sudo systemctl restart fresnel.service
```

---

## Notas operativas

- **Caché de elevaciones** (`/opt/fresnel/cache/elevaciones.json`): crece
  con el uso; no es necesario respaldarla (se reconstruye consultando la
  API de elevación), pero si el VPS va a estar sin red durante una
  sustentación remota, cópiala de antemano desde tu entorno de
  desarrollo para precargar los puntos que vayas a usar.
- **Un solo proceso uvicorn:** la unidad systemd no usa múltiples
  workers a propósito — la caché de elevaciones en memoria/disco no está
  diseñada para escritura concurrente desde varios procesos. Si el
  tráfico lo exigiera más adelante, habría que mover esa caché a algo
  con control de concurrencia (ej. SQLite con locking) antes de escalar
  a más de un worker.
- **Logs de la aplicación:** `sudo journalctl -u fresnel.service`.
- **Logs de nginx:** `/var/log/nginx/access.log` y `/var/log/nginx/error.log`.
- **Certificado HTTPS:** se renueva solo (timer de certbot); revisa una
  vez con `sudo certbot renew --dry-run` para confirmar que el proceso de
  renovación funciona.
