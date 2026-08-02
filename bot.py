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
from datetime import datetime
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
GAME_CONF_FILE = "game.conf"
STEAMCMD = Path(os.getenv("STEAMCMD") or str(GAME_SERVERS_DIR / "steamcmd/steamcmd.sh"))
BACKUP_DIR = Path(os.getenv("BACKUP_DIR") or str(GAME_SERVERS_DIR / "backups"))
BACKUP_KEEP = int(os.getenv("BACKUP_KEEP") or 10)
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


def game_conf(game: Path) -> dict[str, str]:
    """Lit le game.conf du jeu (APPID, PLATFORM, PORTS, SAVE_DIRS)."""
    conf_file = game / GAME_CONF_FILE
    conf = {}
    if conf_file.is_file():
        for line in conf_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                conf[key.strip().upper()] = value.strip()
    return conf


def game_ports(game: Path) -> list[tuple[int, str]]:
    """Ports déclarés dans game.conf (PORTS=8211/udp 2456/udp)."""
    ports = []
    for entry in game_conf(game).get("PORTS", "").split():
        port, _, proto = entry.partition("/")
        if port.isdigit() and proto.upper() in ("TCP", "UDP"):
            ports.append((int(port), proto.upper()))
    return ports


async def update_game(game: Path) -> tuple[str, str]:
    """Met à jour le jeu via SteamCMD. Retourne (résultat, message).

    Sans « validate » : SteamCMD ne touche qu'aux fichiers du dépôt Steam,
    jamais aux configurations locales (PalWorldSettings.ini, start.sh,
    enshrouded_server.json… ne sont pas dans le dépôt).
    """
    appid = game_conf(game).get("APPID")
    if not appid:
        return "skip", ""
    if not STEAMCMD.is_file():
        return "error", "⚠️ **SteamCMD introuvable, lancement sans mise à jour.**"
    platform = game_conf(game).get("PLATFORM", "linux")
    code, output = await run_cmd(
        "bash", str(STEAMCMD),
        f"+@sSteamCmdForcePlatformType {platform}",
        "+force_install_dir", str(game),
        "+login", "anonymous",
        "+app_update", appid,
        "+quit",
        timeout=1800,
    )
    lower = output.lower()
    if code == 0 and "already up to date" in lower:
        return "uptodate", f"✅ **`{game.name}` est déjà à jour.**"
    if code == 0 and "fully installed" in lower:
        return "updated", f"⬆️ **Mise à jour de `{game.name}` appliquée !**"
    return "error", f"⚠️ **Vérification de mise à jour échouée, lancement quand même :**\n```{clip(output, 400)}```"


async def backup_game(game: Path) -> tuple[Path, float] | None:
    """Archive les sauvegardes du jeu (SAVE_DIRS) dans BACKUP_DIR, garde les N dernières."""
    targets = [t for t in game_conf(game).get("SAVE_DIRS", "").split() if (game / t).exists()]
    if not targets:
        return None
    dest = BACKUP_DIR / game.name
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / f"{game.name}_{datetime.now():%Y-%m-%d_%H%M%S}.tar.gz"
    code, output = await run_cmd(
        "tar", "czf", str(archive), "-C", str(game), *targets, timeout=600
    )
    if code != 0:
        archive.unlink(missing_ok=True)
        print(f"❌ Backup {game.name} échoué : {output}")
        return None
    # Rotation : ne garde que les BACKUP_KEEP archives les plus récentes.
    for old in sorted(dest.glob("*.tar.gz"))[:-BACKUP_KEEP]:
        old.unlink()
    return archive, archive.stat().st_size


def human_size(size_bytes: float) -> str:
    if size_bytes >= 1e9:
        return f"{size_bytes / 1e9:.1f} Go"
    if size_bytes >= 1e6:
        return f"{size_bytes / 1e6:.1f} Mo"
    return f"{size_bytes / 1e3:.0f} Ko"


def get_lan_ip() -> str:
    """Adresse IP locale de la machine sur le réseau domestique."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.168.1.1", 80))
        return s.getsockname()[0]


async def get_public_ip() -> str:
    """Adresse IP publique (dynamique, avec repli sur PUBLIC_IP du .env)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.ipify.org", timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                return (await resp.text()).strip()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return os.getenv("PUBLIC_IP", "?")


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


async def do_start(ctx: commands.Context, game: Path):
    """Vérifie les mises à jour puis lance le serveur."""
    if game_conf(game).get("APPID"):
        await ctx.send(f"🔄 **Vérification des mises à jour de `{game.name}`…**")
        _, message = await update_game(game)
        if message:
            await ctx.send(message)
    await ctx.send(f"🟢 **Démarrage de `{game.name}`…**")
    code, output = await launch_game(game)
    if code == 0:
        ports = ", ".join(f"{p}/{proto}" for p, proto in game_ports(game))
        note = f" 🔓 Ports ouverts sur la box : {ports}" if ports and UPNPC else ""
        await ctx.send(f"✅ **Serveur `{game.name}` lancé !**{note}")
    else:
        await ctx.send(f"❌ **Échec du lancement :**\n```{clip(output)}```")


async def do_stop(ctx: commands.Context, game: Path):
    """Arrête le serveur puis sauvegarde (fichiers au repos, backup fiable)."""
    await ctx.send(f"🛑 **Arrêt de `{game.name}`…**")
    code, output = await run_cmd("systemctl", "--user", "stop", game_unit(game.name.lower()), timeout=90)
    if code == 0:
        await ctx.send(f"✅ **Serveur `{game.name}` arrêté !**")
    else:
        await ctx.send(f"❌ **Échec de l'arrêt :**\n```{clip(output)}```")
    await ctx.send(f"💾 **Sauvegarde de `{game.name}`…**")
    backup = await backup_game(game)
    if backup:
        archive, size = backup
        await ctx.send(f"✅ **Backup créé : `{archive.name}` ({human_size(size)})**")
    else:
        await ctx.send(f"⚠️ **Rien à sauvegarder pour `{game.name}`.**")


@bot.command()
@authorized()
async def start(ctx: commands.Context, name: str):
    """Met à jour puis démarre un serveur : !start palworld"""
    game = await require_game(ctx, name)
    if game is None:
        return
    if await game_running(game.name.lower()):
        await ctx.send(f"⚠️ **Le serveur `{game.name}` tourne déjà !**")
        return
    await do_start(ctx, game)


@bot.command()
@authorized()
async def stop(ctx: commands.Context, name: str):
    """Arrête puis sauvegarde un serveur : !stop palworld"""
    game = await require_game(ctx, name)
    if game is None:
        return
    if not await game_running(game.name.lower()):
        await ctx.send(f"⚠️ **Le serveur `{game.name}` n'est pas en cours d'exécution !**")
        return
    await do_stop(ctx, game)


@bot.command()
@authorized()
async def restart(ctx: commands.Context, name: str):
    """Arrête, sauvegarde, met à jour et relance : !restart palworld"""
    game = await require_game(ctx, name)
    if game is None:
        return
    if await game_running(game.name.lower()):
        await do_stop(ctx, game)
    await do_start(ctx, game)


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


@bot.command(name="serverlist")
async def serverlist_cmd(ctx: commands.Context):
    """Liste les serveurs avec adresse, ports et mot de passe"""
    games = discover_games()
    if not games:
        await ctx.send(f"❌ **Aucun serveur de jeu trouvé dans `{GAME_SERVERS_DIR}`.**")
        return
    ip = await get_public_ip()
    embed = discord.Embed(
        title="🎮 McCloud Server — Liste des serveurs",
        description=f"Adresse publique : `{ip}`",
        color=0x3FB950,
    )
    for name, path in games.items():
        conf = game_conf(path)
        ports = game_ports(path)
        icon = "🟢" if await game_running(name) else "🔴"
        lines = []
        if ports:
            port, proto = ports[0]
            lines.append(f"Adresse : `{ip}:{port}`")
            lines.append("Ports : " + ", ".join(f"`{p}/{pr.lower()}`" for p, pr in ports))
        password = conf.get("PASSWORD", "")
        lines.append(f"Mot de passe : `{password}`" if password else "Mot de passe : aucun")
        if conf.get("NOTE"):
            lines.append(f"ℹ️ {conf['NOTE']}")
        embed.add_field(name=f"{icon} {name}", value="\n".join(lines), inline=True)
    await ctx.send(embed=embed)


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
