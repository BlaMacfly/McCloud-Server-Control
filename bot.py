"""McCloud Server Control — bot Discord de pilotage du serveur McCloud.

Gère les piles Docker Compose de /opt/stacks (Caddy, NextCloud, Jellyfin…),
surveille la température CPU (k10temp via /sys/class/hwmon, sans lm-sensors)
et l'espace disque. Conçu pour Debian 13.
"""

import asyncio
import os
import shutil
from pathlib import Path

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

# ⚙️ Configuration (voir .env.example)
TOKEN = os.getenv("DISCORD_TOKEN")
TEMP_LOG_CHANNEL_ID = int(os.getenv("TEMP_LOG_CHANNEL_ID", "0"))
TEMP_CRITICAL_THRESHOLD = float(os.getenv("TEMP_CRITICAL_THRESHOLD", "80"))
TEMP_CHECK_MINUTES = int(os.getenv("TEMP_CHECK_MINUTES", "10"))
STACKS_DIR = Path(os.getenv("STACKS_DIR", "/opt/stacks"))
DISKS = [p for p in os.getenv("DISKS", "/,/mnt/multimedia").split(",") if p]

# 🔒 IDs Discord autorisés à piloter les services (vide = tout le monde)
ALLOWED_USER_IDS = {
    int(i) for i in os.getenv("ALLOWED_USER_IDS", "").split(",") if i.strip()
}

COMPOSE_FILES = ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


### 🐳 Piles Docker Compose ###
def discover_stacks() -> dict[str, Path]:
    """Détecte les piles Docker Compose présentes dans STACKS_DIR."""
    stacks = {}
    if STACKS_DIR.is_dir():
        for d in sorted(STACKS_DIR.iterdir()):
            if d.is_dir() and any((d / f).is_file() for f in COMPOSE_FILES):
                stacks[d.name] = d
    return stacks


async def run_cmd(*args: str, cwd: Path | None = None, timeout: int = 300) -> tuple[int, str]:
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


async def compose(stack: Path, *args: str, timeout: int = 300) -> tuple[int, str]:
    return await run_cmd("docker", "compose", *args, cwd=stack, timeout=timeout)


def resolve_stack(name: str) -> Path | None:
    return discover_stacks().get(name.lower())


def clip(text: str, limit: int = 1900) -> str:
    """Tronque un texte pour tenir dans un message Discord."""
    return text if len(text) <= limit else "…" + text[-limit:]


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
        await ctx.send("⛔ **Vous n'êtes pas autorisé à piloter le serveur.**")
        return False

    return commands.check(predicate)


async def require_stack(ctx: commands.Context, name: str) -> Path | None:
    stack = resolve_stack(name)
    if stack is None:
        stacks = ", ".join(f"`{s}`" for s in discover_stacks()) or "aucune"
        await ctx.send(f"⚠️ **Pile `{name}` introuvable.** Piles disponibles : {stacks}")
    return stack


### 🕹️ Commandes Discord ###
@bot.command(name="stacks")
async def stacks_cmd(ctx: commands.Context):
    """Liste les piles Docker Compose disponibles"""
    stacks = discover_stacks()
    if not stacks:
        await ctx.send(f"❌ **Aucune pile trouvée dans `{STACKS_DIR}`.**")
        return
    await ctx.send("🐳 **Piles disponibles :** " + ", ".join(f"`{s}`" for s in stacks))


@bot.command()
@authorized()
async def start(ctx: commands.Context, name: str):
    """Démarre une pile : !start jellyfin"""
    stack = await require_stack(ctx, name)
    if stack is None:
        return
    await ctx.send(f"🟢 **Démarrage de `{stack.name}`…**")
    code, output = await compose(stack, "up", "-d")
    if code == 0:
        await ctx.send(f"✅ **Pile `{stack.name}` démarrée !**")
    else:
        await ctx.send(f"❌ **Échec du démarrage de `{stack.name}` :**\n```{clip(output)}```")


@bot.command()
@authorized()
async def stop(ctx: commands.Context, name: str):
    """Arrête une pile : !stop jellyfin"""
    stack = await require_stack(ctx, name)
    if stack is None:
        return
    await ctx.send(f"🛑 **Arrêt de `{stack.name}`…**")
    code, output = await compose(stack, "down")
    if code == 0:
        await ctx.send(f"✅ **Pile `{stack.name}` arrêtée !**")
    else:
        await ctx.send(f"❌ **Échec de l'arrêt de `{stack.name}` :**\n```{clip(output)}```")


@bot.command()
@authorized()
async def restart(ctx: commands.Context, name: str):
    """Redémarre une pile : !restart nextcloud"""
    stack = await require_stack(ctx, name)
    if stack is None:
        return
    await ctx.send(f"🔄 **Redémarrage de `{stack.name}`…**")
    code, output = await compose(stack, "restart")
    if code == 0:
        await ctx.send(f"✅ **Pile `{stack.name}` redémarrée !**")
    else:
        await ctx.send(f"❌ **Échec du redémarrage de `{stack.name}` :**\n```{clip(output)}```")


@bot.command()
@authorized()
async def update(ctx: commands.Context, name: str):
    """Met à jour les images d'une pile : !update nextcloud"""
    stack = await require_stack(ctx, name)
    if stack is None:
        return
    await ctx.send(f"🔄 **Mise à jour des images de `{stack.name}`…**")
    code, output = await compose(stack, "pull", timeout=900)
    if code != 0:
        await ctx.send(f"❌ **Échec du téléchargement des images :**\n```{clip(output)}```")
        return
    code, output = await compose(stack, "up", "-d")
    if code == 0:
        await ctx.send(f"✅ **Pile `{stack.name}` à jour et relancée !**")
    else:
        await ctx.send(f"❌ **Échec du relancement :**\n```{clip(output)}```")


@bot.command()
async def logs(ctx: commands.Context, name: str, lines: int = 20):
    """Affiche les derniers logs d'une pile : !logs caddy 30"""
    stack = await require_stack(ctx, name)
    if stack is None:
        return
    code, output = await compose(stack, "logs", "--tail", str(min(lines, 100)), "--no-color")
    if code != 0 or not output:
        await ctx.send(f"⚠️ **Impossible de lire les logs de `{stack.name}`.**")
        return
    await ctx.send(f"📄 **Logs de `{stack.name}` :**\n```{clip(output)}```")


@bot.command()
async def status(ctx: commands.Context):
    """État des services, température et disques"""
    embed = discord.Embed(title="💻 McCloud — État du serveur", color=0x3FB950)

    stacks = discover_stacks()
    if stacks:
        report = []
        for name, path in stacks.items():
            code, output = await compose(path, "ps", "--format", "{{.Name}}: {{.State}}", timeout=30)
            if code != 0:
                report.append(f"❓ `{name}` : état inconnu")
            elif not output:
                report.append(f"🔴 `{name}` : arrêtée")
            else:
                icon = "🟢" if all("running" in l for l in output.splitlines()) else "🟡"
                report.append(f"{icon} `{name}` : {output.count(chr(10)) + 1} conteneur(s)")
        embed.add_field(name="🐳 Services", value="\n".join(report), inline=False)
    else:
        embed.add_field(name="🐳 Services", value="Aucune pile trouvée", inline=False)

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
@tasks.loop(minutes=TEMP_CHECK_MINUTES)
async def monitor_temp():
    channel = bot.get_channel(TEMP_LOG_CHANNEL_ID)
    if channel is None:
        return
    temperature = get_cpu_temp()
    if temperature is None:
        return
    # N'alerte qu'au-delà du seuil critique pour ne pas inonder le salon.
    if temperature >= TEMP_CRITICAL_THRESHOLD:
        await channel.send(f"🚨 **ALERTE : température critique ({temperature:.1f}°C) !**")


@bot.event
async def on_ready():
    print(f"{bot.user} est connecté et surveille McCloud !")
    if TEMP_LOG_CHANNEL_ID and not monitor_temp.is_running():
        monitor_temp.start()


def main():
    if not TOKEN:
        raise SystemExit("❌ DISCORD_TOKEN manquant : copiez .env.example vers .env et remplissez-le.")
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
