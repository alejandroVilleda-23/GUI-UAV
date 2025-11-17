import paramiko


##Conectar por SSH y ejecutar un comando que devuelva latitud y longitud.
def obtener_gps_ssh(host, username, password, command):
    
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname=host, username=username, password=password)

        stdin, stdout, stderr = ssh.exec_command(command)
        print("okay")
        salida = stdout.read().decode().strip()
        print(salida)
        ssh.close()

        lat_str, lon_str = salida.split()
        return float(lat_str), float(lon_str)
    
    except Exception as e:
        print("Error SSH:", e)
        return None, None