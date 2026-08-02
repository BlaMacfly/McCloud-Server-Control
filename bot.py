"""McCloud Server Control — bot Discord de gestion des serveurs de jeux.

Lance, arrête et surveille les serveurs de jeux installés sur le Bureau
(un sous-dossier par jeu avec un start.sh), surveille la température CPU
(k10temp via /sys/class/hwmon, sans lm-sensors) et l'espace disque.
Conçu pour Debian 13.
"""

import asyncio
import os
import shutil
import socket
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

# ⚙️ Configuration (voir .env.example)
TOKEN = os.getenv("DISCORD_TOKEN")
TEMP_LOG_CHANNEL_ID = int(os.getenv("TEMP_LOG_CHANNEL_ID") or 0)
TEMP_WEBHOOK_URL = os.getenv("TEMP_WEBHOOK_URL", "")
TEMP_CRITICAL_THRESHOLD = float(os.getenv("TEMP_CRITICAL_THRESHOLD") or 80)
TEMP_CHECK_MINUTES = int(os.getenv("TEMP_CHECK_MINUTES") or 10)
GAME_SERVERS_DIR = Path(os.getenv("GAME_SERVERS_DIR") or str(Path.home() / "Bureau/GameServers"))
GAME_START_SCRIPT = "start.sh"
GAME_PORTS_FILE = "ports.conf"
UPNPC = shutil.which("upnpc")
DISKS = [p for p in (os.getenv("DISKS") or "/,/mnt/multimedia").split(",") if p]

# 🔒 IDs Discord autorisés à piloter les serveurs (vide = tout le monde)
ALLOWED_USER_IDS = {
    int(i) for i in os.getenv("ALLOWED_USER_IDS", "").split(",") if i.strip()
}

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


### 🔧 Utilitaires ###
async def run_cmd(*args: str, cwd: Path | None = None, timeout: int = 120) -> tuple[int, str]:
    """Exécute une commande sans bloquer la boucle d'événements."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 1, f"⏱️ Commande interrompue après {timeout}s : {' '.join(args)}"
    return proc.returncode, stdout.decode(errors="replace").strip()


def clip(text: str, limit: int = 1900) -> str:
    """Tronque un texte pour tenir dans un message Discord."""
    return text if len(text) <= limit else "…" + text[-limit:]


### 🎮 Serveurs de jeux ###
def discover_games() -> dict[str, Path]:
    """Détecte les serveurs de jeux : sous-dossiers de GAME_SERVERS_DIR avec un start.sh."""
    games = {}
    if GAME_SERVERS_DIR.is_dir():
        for d in sorted(GAME_SERVERS_DIR.iterdir()):
            if d.is_dir() and (d / GAME_START_SCRIPT).is_file():
                games[d.name.lower()] = d
    return games


def game_unit(name: str) -> str:
    return f"game-{name}"


async def game_running(name: str) -> bool:
    code, _ = await run_cmd("systemctl", "--user", "is-active", "--quiet", game_unit(name), timeout=10)
    return code == 0


def game_ports(game: Path) -> list[tuple[int, str]]:
    """Lit les ports du jeu depuis ports.conf (format : 8211/udp, un par ligne)."""
    ports_file = game / GAME_PORTS_FILE
    ports = []
    if ports_file.is_file():
        for entry in ports_file.read_text().split():
            port, _, proto = entry.partition("/")
            if port.isdigit() and proto.upper() in ("TCP", "UDP"):
                ports.append((int(port), proto.upper()))
    return ports


def get_lan_ip() -> str:
    """Adresse IP locale de la machine sur le réseau domestique."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.168.1.1", 80))
        return s.getsockname()[0]


async def launch_game(game: Path) -> tuple[int, str]:
    name = game.name.lower()
    await run_cmd("systemctl", "--user", "reset-failed", game_unit(name), timeout=10)

    # Ouverture UPnP des ports au démarrage, fermeture à l'arrêt de l'unité
    # (ExecStopPost s'exécute aussi si le serveur plante). Le préfixe « - »
    # évite de bloquer le jeu si la box refuse l'UPnP.
    upnp_props = []
    if UPNPC:
        lan_ip = get_lan_ip()
        for port, proto in game_ports(game):
            upnp_props += [
                "-p", f"ExecStartPre=-{UPNPC} -e {game_unit(name)} -a {lan_ip} {port} {port} {proto}",
                "-p", f"ExecStopPost=-{UPNPC} -d {port} {proto}",
            ]

    return await run_cmd(
        "systemd-run", "--user", "--collect",
        f"--unit={game_unit(name)}",
        f"--working-directory={game}",
        *upnp_props,
        str(game / GAME_START_SCRIPT),
        timeout=60,
    )


### 🌡️ Température CPU (k10temp, sans lm-sensors) ###
def get_cpu_temp() -> float | None:
    """Lit la température CPU dans /sys/class/hwmon (k10temp en priorité)."""
    fallback = None
    for hwmon in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        sensor = hwmon / "temp1_input"
        if not sensor.is_file():
            continue
        try:
            value = int(sensor.read_text()) / 1000
        except (OSError, ValueError):
            continue
        if (hwmon / "name").read_text().strip() == "k10temp":
            return value
        if fallback is None:
            fallback = value
    return fallback


### 💾 Espace disque ###
def get_disk_usage() -> list[tuple[str, float, float, float]]:
    """Retourne (point de montage, total Go, utilisé Go, % utilisé) par disque."""
    usage = []
    for mount in DISKS:
        try:
            total, used, _ = shutil.disk_usage(mount)
        except OSError:
            continue
        usage.append((mount, total / 1e9, used / 1e9, used / total * 100))
    return usage


### 🔒 Contrôle d'accès ###
def authorized():
    async def predicate(ctx: commands.Context) -> bool:
        if not ALLOWED_USER_IDS or ctx.author.id in ALLOWED_USER_IDS:
            return True
        await ctx.send("⛔ **Vous n'êtes pas autorisé à piloter les serveurs.**")
        return False

    return commands.check(predicate)


async def require_game(ctx: commands.Context, name: str) -> Path | None:
    games = discover_games()
    game = games.get(name.lower())
    if game is None:
        available = ", ".join(f"`{g}`" for g in games) or "aucun"
        await ctx.send(f"⚠️ **Serveur `{name}` introuvable.** Disponibles : {available}")
    return game


### 🕹️ Commandes Discord ###
@bot.command(name="games")
async def games_cmd(ctx: commands.Context):
    """Liste les serveurs de jeux et leur état"""
    games = discover_games()
    if not games:
        await ctx.send(
            f"❌ **Aucun serveur de jeu trouvé.** Placez chaque jeu dans "
            f"`{GAME_SERVERS_DIR}/<nom>/` avec un script `{GAME_START_SCRIPT}`."
        )
        return
    lines = []
    for name in games:
        icon = "🟢" if await game_running(name) else "🔴"
        lines.append(f"{icon} `{name}`")
    await ctx.send("🎮 **Serveurs de jeux :**\n" + "\n".join(lines))


@bot.command()
@authorized()
async def start(ctx: commands.Context, name: str):
    """Démarre un serveur de jeu : !start palworld"""
    game = await require_game(ctx, name)
    if game is None:
        return
    if await game_running(game.name.lower()):
        await ctx.send(f"⚠️ **Le serveur `{game.name}` tourne déjà !**")
        return
    await ctx.send(f"🟢 **Démarrage de `{game.name}`…**")
    code, output = await launch_game(game)
    if code == 0:
        ports = ", ".join(f"{p}/{proto}" for p, proto in game_ports(game))
        note = f" 🔓 Ports ouverts sur la box : {ports}" if ports and UPNPC else ""
        await ctx.send(f"✅ **Serveur `{game.name}` lancé !**{note}")
    else:
        await ctx.send(f"❌ **Échec du lancement :**\n```{clip(output)}```")


@bot.command()
@authorized()
async def stop(ctx: commands.Context, name: str):
    """Arrête un serveur de jeu : !stop palworld"""
    game = await require_game(ctx, name)
    if game is None:
        return
    name = game.name.lower()
    if not await game_running(name):
        await ctx.send(f"⚠️ **Le serveur `{name}` n'est pas en cours d'exécution !**")
        return
    await ctx.send(f"🛑 **Arrêt de `{name}`…**")
    code, output = await run_cmd("systemctl", "--user", "stop", game_unit(name), timeout=90)
    if code == 0:
        await ctx.send(f"✅ **Serveur `{name}` arrêté !**")
    else:
        await ctx.send(f"❌ **Échec de l'arrêt :**\n```{clip(output)}```")


@bot.command()
@authorized()
async def restart(ctx: commands.Context, name: str):
    """Redémarre un serveur de jeu : !restart palworld"""
    game = await require_game(ctx, name)
    if game is None:
        return
    if await game_running(game.name.lower()):
        await ctx.send(f"🛑 **Arrêt de `{game.name}`…**")
        await run_cmd("systemctl", "--user", "stop", game_unit(game.name.lower()), timeout=90)
    await ctx.send(f"🟢 **Démarrage de `{game.name}`…**")
    code, output = await launch_game(game)
    if code == 0:
        await ctx.send(f"✅ **Serveur `{game.name}` relancé !**")
    else:
        await ctx.send(f"❌ **Échec du lancement :**\n```{clip(output)}```")


@bot.command()
async def logs(ctx: commands.Context, name: str, lines: int = 20):
    """Affiche les derniers logs d'un serveur : !logs palworld 30"""
    game = await require_game(ctx, name)
    if game is None:
        return
    code, output = await run_cmd(
        "journalctl", "--user", "-u", game_unit(game.name.lower()),
        "-n", str(min(lines, 100)), "--no-pager", "-o", "cat",
        timeout=30,
    )
    if code != 0 or not output:
        await ctx.send(f"⚠️ **Aucun log disponible pour `{game.name}`.**")
        return
    await ctx.send(f"📄 **Logs de `{game.name}` :**\n```{clip(output)}```")


@bot.command()
async def status(ctx: commands.Context):
    """État des serveurs de jeux, température et disques"""
    embed = discord.Embed(title="💻 McCloud — État du serveur", color=0x3FB950)

    games = discover_games()
    if games:
        report = []
        for name in games:
            icon = "🟢" if await game_running(name) else "🔴"
            state = "en ligne" if icon == "🟢" else "arrêté"
            report.append(f"{icon} `{name}` : {state}")
        embed.add_field(name="🎮 Serveurs de jeux", value="\n".join(report), inline=False)
    else:
        embed.add_field(
            name="🎮 Serveurs de jeux",
            value=f"Aucun jeu installé dans `{GAME_SERVERS_DIR}`",
            inline=False,
        )

    temp = get_cpu_temp()
    if temp is not None:
        icon = "🚨" if temp >= TEMP_CRITICAL_THRESHOLD else "🌡️"
        embed.add_field(name="Température CPU", value=f"{icon} {temp:.1f}°C", inline=True)

    for mount, total, used, percent in get_disk_usage():
        embed.add_field(
            name=f"💾 {mount}",
            value=f"{used:.0f} / {total:.0f} Go ({percent:.0f}%)",
            inline=True,
        )

    await ctx.send(embed=embed)


@bot.command()
async def temp(ctx: commands.Context):
    """Température CPU actuelle"""
    temperature = get_cpu_temp()
    if temperature is None:
        await ctx.send("⚠️ **Impossible de lire la température CPU.**")
    elif temperature >= TEMP_CRITICAL_THRESHOLD:
        await ctx.send(f"🚨 **Température critique : {temperature:.1f}°C !**")
    else:
        await ctx.send(f"🌡️ **Température CPU : {temperature:.1f}°C**")


@bot.command()
async def disk(ctx: commands.Context):
    """Espace disque des volumes surveillés"""
    usage = get_disk_usage()
    if not usage:
        await ctx.send("⚠️ **Aucun disque accessible.**")
        return
    lines = [
        f"💾 `{mount}` : {used:.0f} / {total:.0f} Go ({percent:.0f}%)"
        for mount, total, used, percent in usage
    ]
    await ctx.send("\n".join(lines))


### 🌡️ Surveillance automatique ###
async def send_temp_alert(message: str):
    """Envoie une alerte via le webhook dédié, sinon dans le salon configuré."""
    if TEMP_WEBHOOK_URL:
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(TEMP_WEBHOOK_URL, json={"content": message})
            return
        except aiohttp.ClientError as e:
            print(f"❌ Webhook température injoignable : {e}")
    channel = bot.get_channel(TEMP_LOG_CHANNEL_ID)
    if channel is not None:
        await channel.send(message)


@tasks.loop(minutes=TEMP_CHECK_MINUTES)
async def monitor_temp():
    temperature = get_cpu_temp()
    if temperature is None:
        return
    # N'alerte qu'au-delà du seuil critique pour ne pas inonder le salon.
    if temperature >= TEMP_CRITICAL_THRESHOLD:
        await send_temp_alert(f"🚨 **ALERTE : température critique ({temperature:.1f}°C) !**")


@bot.event
async def on_ready():
    print(f"{bot.user} est connecté et surveille McCloud !")
    if (TEMP_WEBHOOK_URL or TEMP_LOG_CHANNEL_ID) and not monitor_temp.is_running():
        monitor_temp.start()


def main():
    if not TOKEN:
        raise SystemExit("❌ DISCORD_TOKEN manquant : copiez .env.example vers .env et remplissez-le.")
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
