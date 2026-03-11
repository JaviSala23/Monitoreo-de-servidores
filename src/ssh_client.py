"""
Cliente SSH basado en Paramiko para conexiones persistentes con los servidores.
"""
from typing import Tuple, Optional
import paramiko


class SSHClient:
    def __init__(self, host: str, port: int, username: str, password: str) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._client: Optional[paramiko.SSHClient] = None

    def connect(self) -> bool:
        try:
            client = paramiko.SSHClient()
            # AutoAddPolicy acepta host keys desconocidos (tradeoff de conveniencia vs seguridad)
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=10,
                banner_timeout=15,
                auth_timeout=15,
                look_for_keys=False,
                allow_agent=False,
            )
            self._client = client
            return True
        except Exception:
            self._client = None
            return False

    def is_connected(self) -> bool:
        if self._client is None:
            return False
        transport = self._client.get_transport()
        return transport is not None and transport.is_active()

    def execute(self, command: str, timeout: int = 12) -> Tuple[str, str]:
        """Ejecuta un comando shell en el servidor remoto."""
        if not self.is_connected():
            if not self.connect():
                return "", "No se pudo conectar"
        try:
            stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace").strip()
            err = stderr.read().decode("utf-8", errors="replace").strip()
            return out, err
        except Exception as e:
            self._client = None
            return "", str(e)

    def execute_python(self, script: str, timeout: int = 20) -> Tuple[str, str]:
        """Envía un script Python al servidor y lo ejecuta via stdin de python3."""
        if not self.is_connected():
            if not self.connect():
                return "", "No se pudo conectar"
        try:
            channel = self._client.get_transport().open_session()
            channel.settimeout(timeout)
            channel.exec_command("python3")
            channel.sendall(script.encode("utf-8"))
            channel.shutdown_write()

            out_chunks: list[bytes] = []
            err_chunks: list[bytes] = []

            while not channel.exit_status_ready():
                if channel.recv_ready():
                    out_chunks.append(channel.recv(4096))
                if channel.recv_stderr_ready():
                    err_chunks.append(channel.recv_stderr(4096))

            while channel.recv_ready():
                out_chunks.append(channel.recv(4096))
            while channel.recv_stderr_ready():
                err_chunks.append(channel.recv_stderr(4096))

            channel.close()
            return (
                b"".join(out_chunks).decode("utf-8", errors="replace").strip(),
                b"".join(err_chunks).decode("utf-8", errors="replace").strip(),
            )
        except Exception as e:
            self._client = None
            return "", str(e)

    def disconnect(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
