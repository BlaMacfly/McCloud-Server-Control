import discord
from discord.ext import commands, tasks
import subprocess
import os
import asyncio

# Token du bot
TOKEN = "Token ici"

# 📢 ID des salons Discord
TEMP_LOG_CHANNEL_ID = ID ici  
SERVER_STATUS_CHANNEL_ID = ID ici 

# 🌡️ Seuil critique de température CPU
TEMP_CRITICAL_THRESHOLD = 80  

# 📂 Chemins des serveurs
SERVERS = {
    "palworld": {
        "path": "/home/UbuntuPC/Palworld/PalServer",
        "script": "PalServer.sh",
        "app_id": 2394010
    },
    "valheim": {
        "path": "/home/UbuntuPC/valheim/server",
        "script": "start_server.sh",
        "app_id": 896660
    },
    "ark": {
        "path": "/home/UbuntuPC/ark/server",
        "script": "start_ark.sh",
        "app_id": 376030
    },
    "minecraft": {
        "path": "/home/UbuntuPC/Minecraft/bedrock-server",
        "script": "bedrock_server",  # pas de .sh ici
        "app_id": None  # pas utilisé pour Minecraft Bedrock
    }
}


STEAMCMD_PATH = "/home/UbuntuPC/steamcmd/steamcmd.sh"

# 🎮 Configuration du bot Discord
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

### 📌 Vérifications des serveurs ###
def check_status(session_name):
    """ Vérifie si un serveur tourne via tmux """
    try:
        result = subprocess.run(["tmux", "has-session", "-t", session_name], capture_output=True, text=True)
        return result.returncode == 0
    except:
        return False

def get_running_server():
    """ Vérifie si un serveur est déjà actif et retourne son nom """
    for server in SERVERS.keys():
        if check_status(server):
            return server
    return None

### 🔄 Mise à jour du serveur via SteamCMD ###
def update_server(app_id):
    try:
        print(f"🔄 Mise à jour de l'AppID {app_id} en cours...")
        result = subprocess.run(
            [STEAMCMD_PATH, "+login", "anonymous", "+force_install_dir", SERVERS["palworld"]["path"], f"+app_update {app_id} validate", "+quit"],
            capture_output=True, text=True, check=True
        )
        print(f"📝 Résultat de la mise à jour :\n{result.stdout}")
        return "success" in result.stdout.lower() or "fully installed" in result.stdout.lower() or "already up to date" in result.stdout.lower()
    except subprocess.CalledProcessError as e:
        print(f"❌ Erreur SteamCMD : {e}")
        return False

### 🚀 Démarrer un serveur ###
async def start_server(ctx, session_name):
    running_server = get_running_server()
    if running_server:
        await ctx.send(f"⚠️ **Le serveur {running_server} est déjà en cours d'exécution. Arrêtez-le avant de lancer {session_name}.**")
        return

    await ctx.send(f"🛑 **Arrêt du serveur {session_name} avant la mise à jour...**")
    subprocess.run(["tmux", "kill-session", "-t", session_name], capture_output=True, text=True)

    await ctx.send(f"🔄 **Vérification des mises à jour pour {session_name}...**")
    updated = await update_server(SERVERS[session_name]["app_id"])
    if updated:
        await ctx.send(f"✅ **Mise à jour de {session_name} appliquée avec succès !**")

    await ctx.send(f"🟢 **Démarrage du serveur {session_name}...**")
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", session_name, f"bash -c 'cd {SERVERS[session_name]['path']} && ./{SERVERS[session_name]['script']}; bash'"],
        capture_output=True, text=True
    )
    await ctx.send(f"✅ **Serveur {session_name} lancé !**")

    # ✅ Attendre plus longtemps pour que le serveur démarre et génère les logs
    await asyncio.sleep(20)

    # ✅ Récupérer la version après le démarrage
    version = get_server_version(session_name)
    await ctx.send(f"📝 **Version du serveur {session_name} : {version}**")


### 🔄 Mise à jour du serveur via SteamCMD ###
async def update_server(app_id):
    try:
        await asyncio.sleep(1)  # Petit délai pour éviter les conflits avec SteamCMD
        print(f"🔄 Mise à jour de l'AppID {app_id} en cours...")

        process = await asyncio.create_subprocess_exec(
            STEAMCMD_PATH, "+force_install_dir", SERVERS["palworld"]["path"], "+login", "anonymous",
            f"+app_update {app_id} validate", "+quit",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()
        output = stdout.decode() + stderr.decode()
        print(f"📝 Résultat de la mise à jour :\n{output}")

        return "success" in output.lower() or "fully installed" in output.lower() or "already up to date" in output.lower()
    except Exception as e:
        print(f"❌ Erreur SteamCMD : {e}")
        return False


### 📄 Récupérer la version du serveur ###
def get_server_version(session_name):
    """ Récupère la version du serveur via les logs """
    log_file = f"{SERVERS[session_name]['path']}/PalServer.log"  # Ajuste en fonction de ton serveur

    try:
        with open(log_file, "r") as f:
            lines = f.readlines()
            for line in lines:
                if "version" in line.lower():  # Modifie si nécessaire pour détecter la bonne ligne
                    return line.strip()
        return "Version inconnue"
    except FileNotFoundError:
        return "Log introuvable"



### 🛑 Arrêter un serveur ###
async def stop_server(ctx, session_name):
    if not check_status(session_name):
        await ctx.send(f"⚠️ **Le serveur {session_name} n'est pas en cours d'exécution !**")
        return
    
    await ctx.send(f"🛑 **Arrêt du serveur {session_name}...**")
    subprocess.run(["tmux", "kill-session", "-t", session_name], capture_output=True, text=True)
    await ctx.send(f"✅ **Serveur {session_name} arrêté !**")

### 🌡️ Surveillance de la température CPU ###
def get_cpu_temp():
    try:
        result = subprocess.run(["sensors"], capture_output=True, text=True, check=True)
        for line in result.stdout.split("\n"):
            if "Tctl" in line or "edge" in line:
                temp = [s for s in line.split() if s.replace('+', '').replace('°C', '').replace('.', '').isdigit()]
                if temp:
                    return float(temp[0].replace("°C", ""))
        return None
    except Exception:
        return None

@tasks.loop(minutes=10)
async def send_cpu_temp():
    channel = bot.get_channel(TEMP_LOG_CHANNEL_ID)
    if channel:
        temperature = get_cpu_temp()
        if temperature is not None:
            await channel.send(f"🌡️ Température actuelle du serveur : {temperature}°C")
            if temperature >= TEMP_CRITICAL_THRESHOLD:
                await channel.send(f"🚨 **ALERTE : Température critique détectée ! ({temperature}°C)** 🚨")
        else:
            await channel.send("⚠️ Impossible de récupérer la température du serveur.")

### 🕹️ Commandes Discord ###
@bot.command()
async def pstart(ctx):
    """ Commande pour démarrer Palworld """
    await start_server(ctx, "palworld")

@bot.command()
async def vstart(ctx):
    """ Commande pour démarrer Valheim """
    await start_server(ctx, "valheim")

@bot.command()
async def astart(ctx):
    """ Commande pour démarrer ARK """
    await start_server(ctx, "ark")
    
@bot.command()
async def mstart(ctx):
    try:
        session_name = "minecraft"
        path = SERVERS[session_name]["path"]

        # Vérifie si un autre serveur est actif
        running_server = get_running_server()
        if running_server and running_server != session_name:
            await ctx.send(f"⚠️ **Le serveur {running_server} est déjà en cours d'exécution. Arrêtez-le avec `!{running_server[0]}stop` avant de lancer Minecraft.**")
            return

        # Vérifie si Minecraft est déjà lancé
        if check_status(session_name):
            await ctx.send("⚠️ **Le serveur Minecraft est déjà en cours d'exécution !**")
            return

        # Lancement du serveur Minecraft dans tmux
        await ctx.send("🚀 Lancement du serveur Minecraft Bedrock...")
        result = subprocess.run(
            ["tmux", "new-session", "-d", "-s", session_name,
             f"bash -c 'cd {path} && ./bedrock_server; echo \"App terminé. Appuyez sur une touche pour quitter...\"; read -n1'"],
            capture_output=True, text=True
        )

        # Log dans Discord en cas d'erreur
        if result.stderr:
            await ctx.send(f"⚠️ Erreur au lancement : {result.stderr[-500:]}")

        await ctx.send("✅ **Serveur Minecraft Bedrock lancé !**")

    except Exception as e:
        await ctx.send(f"❌ Une erreur est survenue dans mstart : {e}")

@bot.command()
async def pstop(ctx):
    """ Commande pour arrêter Palworld """
    await stop_server(ctx, "palworld")

@bot.command()
async def vstop(ctx):
    """ Commande pour arrêter Valheim """
    await stop_server(ctx, "valheim")

@bot.command()
async def astop(ctx):
    """ Commande pour arrêter ARK """
    await stop_server(ctx, "ark")
    
@bot.command()
async def mstop(ctx):
    session_name = "minecraft"
    if not check_status(session_name):
        await ctx.send(f"⚠️ **Le serveur {session_name} n'est pas en cours d'exécution !**")
        return

    await ctx.send(f"🛑 **Arrêt du serveur {session_name}...**")
    subprocess.run(["tmux", "kill-session", "-t", session_name], capture_output=True, text=True)
    await ctx.send(f"✅ **Serveur {session_name} arrêté !**")

@bot.command()
async def status(ctx):
    """ Vérifie quel serveur est en cours d'exécution """
    running_server = get_running_server()
    if running_server:
        await ctx.send(f"🟢 **Le serveur {running_server} est actuellement en ligne !**")
    else:
        await ctx.send("❌ **Aucun serveur n'est en cours d'exécution.**")

### 🏁 Lancement du bot ###
@bot.event
async def on_ready():
    print(f"{bot.user} est connecté et surveille les serveurs !")
    send_cpu_temp.start()

bot.run(TOKEN)
