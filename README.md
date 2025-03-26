# 💻 McCloud Server Control

![McCloud Server Contrôle](./McCloud%20Server%20Contr%C3%B4le.png)

## 📜 Licence

Ce projet est sous la **MIT License**. Voir le fichier [LICENSE](./LICENSE) pour plus de détails.


🎮 **McCloud Server Control** est un bot Discord développé par BlaMacfly en Python permettant de gérer plusieurs serveurs de jeux Linux **conçus spécifiquement pour un système Ubuntu OS**.  
Il permet de **lancer, arrêter, surveiller et mettre à jour** les serveurs suivants :

- Palworld
- Valheim
- ARK: Survival Evolved
- Minecraft Bedrock Edition

---

## ⚙️ Fonctionnalités principales

- 🟢 Lancement et arrêt des serveurs via Discord (`!pstart`, `!pstop`, etc.)
- 🔄 Mise à jour automatique des serveurs via SteamCMD (sauf Minecraft)
- 💬 Statut du serveur en temps réel avec `!status`
- 🌡️ Suivi automatique de la température CPU (via `lm-sensors`)
- ✅ Utilisation de `tmux` pour exécution en arrière-plan
- 📦 Configuration sécurisée via `.env`
- 🐧 **Optimisé pour un environnement Ubuntu OS**

## 🛑 Commandes disponibles dans Discord

| Commande   | Action                                  |
|------------|------------------------------------------|
| `!pstart`  | Démarre le serveur **Palworld**          |
| `!pstop`   | Arrête le serveur **Palworld**           |
| `!vstart`  | Démarre le serveur **Valheim**           |
| `!vstop`   | Arrête le serveur **Valheim**            |
| `!astart`  | Démarre le serveur **ARK**               |
| `!astop`   | Arrête le serveur **ARK**                |
| `!mstart`  | Démarre le serveur **Minecraft Bedrock** |
| `!mstop`   | Arrête le serveur **Minecraft Bedrock**  |
| `!status`  | Affiche le serveur actuellement actif    |


---
---

## 📦 Dépendances

Dépendances système (Ubuntu requis)

sudo apt install tmux lm-sensors


### 📚 Python (installées avec pip)

```bash
pip install discord.py python-dotenv

## 📁 Installation

```bash
git clone https://github.com/BlaMacfly/McCloud-Server-Control.git
cd McCloud-Server-Control
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

### 🚀 Utilisation

## Activez votre environnement virtuel et lancez le bot :

source venv/bin/activate
python bot.py

