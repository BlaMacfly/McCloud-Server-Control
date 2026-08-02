# 💻 McCloud Server Control

![McCloud Server Contrôle](./McCloud%20Server%20Contr%C3%B4le.png)

🐳 **McCloud Server Control** est un bot Discord développé par BlaMacfly en Python
permettant de piloter le serveur domestique **McCloud** (Debian 13) : il gère les
piles **Docker Compose** de `/opt/stacks` (Caddy, NextCloud, Jellyfin…), surveille
la **température CPU** et l'**espace disque**.

Les piles sont détectées automatiquement : toute nouvelle pile ajoutée dans
`/opt/stacks` (par exemple via Dockge) est immédiatement pilotable, sans modifier
le code.

---

## ⚙️ Fonctionnalités principales

- 🐳 Démarrage, arrêt, redémarrage et mise à jour des piles Docker via Discord
- 🔍 Détection automatique des piles présentes dans `/opt/stacks`
- 📊 État global du serveur (services, température, disques) avec `!status`
- 📄 Consultation des logs des conteneurs depuis Discord
- 🎮 Gestion de serveurs de jeux installés localement (détection automatique,
  lancement via `systemd-run`, sans `tmux`)
- 🌡️ Alerte automatique en cas de température CPU critique, via webhook Discord
  ou salon dédié (lecture directe de `/sys/class/hwmon`, sans `lm-sensors`)
- 💾 Suivi de l'espace disque (`/` et `/mnt/multimedia`)
- 🔒 Liste blanche optionnelle d'utilisateurs Discord autorisés
- 📦 Configuration via `.env`, aucun secret dans le code

## 🕹️ Commandes disponibles dans Discord

| Commande              | Action                                                |
|-----------------------|-------------------------------------------------------|
| `!stacks`             | Liste les piles Docker disponibles                    |
| `!start <pile>`       | Démarre une pile (`!start jellyfin`)                  |
| `!stop <pile>`        | Arrête une pile (`!stop jellyfin`)                    |
| `!restart <pile>`     | Redémarre une pile (`!restart nextcloud`)             |
| `!update <pile>`      | Met à jour les images et relance la pile              |
| `!logs <pile> [n]`    | Affiche les `n` dernières lignes de logs (20 défaut)  |
| `!games`              | Liste les serveurs de jeux et leur état               |
| `!gstart <jeu>`       | Démarre un serveur de jeu (`!gstart palworld`)        |
| `!gstop <jeu>`        | Arrête un serveur de jeu (`!gstop palworld`)          |
| `!status`             | État des services, jeux, température CPU et disques   |
| `!temp`               | Température CPU actuelle                              |
| `!disk`               | Espace disque des volumes surveillés                  |

---

## 📦 Prérequis

- Debian 12/13 avec Docker et le plugin Docker Compose
- L'utilisateur exécutant le bot doit appartenir au groupe `docker` :

```bash
sudo usermod -aG docker $USER
```

(déconnexion/reconnexion nécessaire pour que le groupe soit pris en compte)

## 📁 Installation

```bash
git clone https://github.com/BlaMacfly/McCloud-Server-Control.git
cd McCloud-Server-Control
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # puis renseignez DISCORD_TOKEN et les salons
```

## 🎮 Serveurs de jeux

Chaque serveur de jeu s'installe dans son propre sous-dossier de
`GAME_SERVERS_DIR` (par défaut `~/Bureau/GameServers`) avec un script
`start.sh` exécutable qui lance le serveur :

```
GameServers/
├── palworld/
│   └── start.sh
└── minecraft/
    └── start.sh
```

Le bot les détecte automatiquement et les pilote avec `!gstart` / `!gstop`
(exécution en unité systemd utilisateur, pas besoin de `tmux`).

## 🚀 Utilisation

```bash
source venv/bin/activate
python bot.py
```

### 🔁 Lancement automatique au démarrage (systemd)

```bash
sudo cp mccloud-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mccloud-bot
```

---

## 📜 Licence

Ce projet est sous la **MIT License**. Voir le fichier [LICENSE](./LICENSE) pour plus de détails.
