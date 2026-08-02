# 💻 McCloud Server Control

![McCloud Server Contrôle](./McCloud%20Server%20Contr%C3%B4le.png)

🎮 **McCloud Server Control** est un bot Discord développé par BlaMacfly en Python
permettant de **lancer, arrêter et surveiller des serveurs de jeux** hébergés sur
le serveur domestique **McCloud** (Debian 13). Il surveille aussi la
**température CPU** et l'**espace disque**.

Les serveurs de jeux sont détectés automatiquement : chaque jeu installé dans
`GAME_SERVERS_DIR` avec un script `start.sh` devient immédiatement pilotable,
sans modifier le code.

---

## ⚙️ Fonctionnalités principales

- 🎮 Lancement, arrêt et redémarrage des serveurs de jeux via Discord
- 🔍 Détection automatique des jeux installés (un dossier + un `start.sh`)
- 🧰 Exécution en unités systemd utilisateur — pas besoin de `tmux` ni de root
- 📄 Consultation des logs de chaque serveur depuis Discord
- 📊 État global (serveurs, température, disques) avec `!status`
- 🌡️ Alerte automatique en cas de température CPU critique, via webhook Discord
  ou salon dédié (lecture directe de `/sys/class/hwmon`, sans `lm-sensors`)
- 💾 Suivi de l'espace disque
- 🔒 Liste blanche optionnelle d'utilisateurs Discord autorisés
- 📦 Configuration via `.env`, aucun secret dans le code

## 🕹️ Commandes disponibles dans Discord

| Commande            | Action                                                 |
|---------------------|--------------------------------------------------------|
| `!games`            | Liste les serveurs de jeux et leur état                |
| `!start <jeu>`      | Démarre un serveur (`!start palworld`)                 |
| `!stop <jeu>`       | Arrête un serveur (`!stop palworld`)                   |
| `!restart <jeu>`    | Redémarre un serveur (`!restart palworld`)             |
| `!logs <jeu> [n]`   | Affiche les `n` dernières lignes de logs (20 défaut)   |
| `!status`           | État des serveurs, température CPU et disques          |
| `!temp`             | Température CPU actuelle                               |
| `!disk`             | Espace disque des volumes surveillés                   |

---

## 🎮 Installation d'un serveur de jeu

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

Exemple de `start.sh` pour Minecraft Bedrock :

```bash
#!/bin/bash
cd "$(dirname "$0")"
exec ./bedrock_server
```

Le bot le détecte automatiquement — `!games` le liste, `!start minecraft` le lance.

## 📁 Installation du bot

```bash
git clone https://github.com/BlaMacfly/McCloud-Server-Control.git
cd McCloud-Server-Control
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # puis renseignez DISCORD_TOKEN et le webhook
```

## 🚀 Utilisation

```bash
source venv/bin/activate
python bot.py
```

### 🔁 Lancement automatique au démarrage (systemd)

```bash
mkdir -p ~/.config/systemd/user
cp mccloud-bot.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now mccloud-bot
```

---

## 📜 Licence

Ce projet est sous la **MIT License**. Voir le fichier [LICENSE](./LICENSE) pour plus de détails.
