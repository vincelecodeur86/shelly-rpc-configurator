# Shelly Configurator

Utilitaire graphique pour configurer automatiquement des **Shelly Plus 1PM** (firmware **1.7.1 minimum**)
par leur API RPC (`http://<ip>/rpc`). Une seule base de code Python, sans dépendance externe, qui
fonctionne sous **Windows 7 SP1 à 11** et sous **Linux**.

## Fonctionnement

1. **Premier lancement** : l'utilisateur renseigne la configuration souhaitée (onglets Appareil, Wi-Fi,
   Point d'accès, MQTT, WebSocket, Sécurité, Certificats TLS, Relais / Entrée, Options). Elle est
   enregistrée ; les mots de passe et la clé privée TLS sont **chiffrés**.
2. **Lancements suivants** : l'outil affiche un résumé et demande seulement **Garder** ou **Modifier**.
3. On ajoute un Shelly par son adresse IP ou son nom, ou par **recherche sur le réseau**, puis on clique
   sur **Configurer les Shelly sélectionnés**. Deux façons de joindre le Shelly :
   * par **son point d'accès** : le PC se connecte au Wi-Fi `shellyplus1pm-xxxx` émis par le Shelly
     (Shelly neuf ou réinitialisé), adresse `192.168.33.1` ;
   * par **le réseau Wi-Fi auquel le Shelly est déjà connecté** : adresse IP donnée par la box, ou nom
     `shellyplus1pm-xxxx.local`, ou bouton **Rechercher les Shelly** (annonces mDNS puis balayage des
     adresses).

### Shelly joint par votre réseau Wi-Fi

L'outil détecte par quel chemin il joint le Shelly (`WiFi.GetStatus`). Si c'est par le réseau Wi-Fi et
que la configuration enregistrée change le réseau ou l'adresse du Shelly (autre SSID, autre IP fixe,
passage en DHCP, Wi-Fi client désactivé), la connexion va être coupée : **avant toute modification**,
l'outil l'explique et propose **Continuer**, **Appliquer sans le Wi-Fi** ou **Annuler ce Shelly**.
L'avertissement est renforcé si le point d'accès du Shelly sera aussi désactivé (une erreur de mot de
passe Wi-Fi rendrait alors le Shelly injoignable). En série, le choix peut être appliqué aux suivants.

Après l'envoi du Wi-Fi puis après le redémarrage, l'outil **recherche le Shelly** (même identifiant) à sa
nouvelle adresse fixe, à la même adresse et par son nom `.local` (mDNS intégré, utile sous Windows 7 qui
ne résout pas les noms `.local`). S'il le retrouve, il termine la vérification et indique la nouvelle
adresse ; sinon, il le signale (normal si le PC n'est pas sur le nouveau réseau). Ces deux comportements
se règlent dans l'onglet **Options** (confirmation, délai de recherche).

Pour chaque appareil, l'outil :

| Étape | Détail |
|---|---|
| Identification | `Shelly.GetDeviceInfo` : refuse tout appareil qui n'est pas un Plus 1PM (application `Plus1PM`, modèles `SNSW-001P16EU` / `SNSW-001P15UL`) |
| Firmware | si < 1.7.1, propose une mise à jour **en ligne** (`Shelly.Update` stable) ou **depuis un fichier .zip** servi par le PC, attend le redémarrage et revérifie |
| Sauvegarde | config actuelle complète (`Shelly.GetConfig` + `GetStatus`) dans un JSON horodaté |
| Envoi | Sys, Cloud, Bluetooth, relais, entrée, certificats TLS, MQTT, WebSocket, mot de passe (`Shelly.SetAuth`), puis **Wi-Fi en dernier** (la connexion peut être coupée) |
| Vérification | relecture de chaque section (`*.GetConfig`) et comparaison avec ce qui a été demandé |
| Redémarrage | si le Shelly le demande (réglable : toujours / jamais) |

Toute erreur affiche une **fenêtre d'erreur** et est écrite dans le **journal**. Plusieurs Shelly peuvent
être configurés à la suite (bilan final par appareil). Pour les Shelly configurés un par un via leur point
d'accès, l'outil propose d'enchaîner sur le suivant à la même adresse.

### Variables pour les configurations en série

Dans le nom de l'appareil, du relais, de l'entrée, l'identifiant client et le préfixe MQTT :
`{id}` (ex. `shellyplus1pm-a8032ab12345`), `{mac}`, `{mac6}` (6 derniers caractères de la MAC), `{n}`
(numéro de l'appareil, réglable dans la fenêtre principale). En IP fixe, l'option « incrémenter » donne
l'IP saisie au n°1, IP + 1 au n°2, etc.

## Fichiers de l'utilisateur

| | Windows | Linux |
|---|---|---|
| Configuration | `%APPDATA%\ShellyConfigurator\config.json` | `~/.config/shelly-configurator/config.json` |
| Journaux | `%APPDATA%\ShellyConfigurator\logs\` | `~/.local/share/shelly-configurator/logs/` |
| Sauvegardes | `%APPDATA%\ShellyConfigurator\backups\` | `~/.local/share/shelly-configurator/backups/` |

**Chiffrement** : sous Windows, DPAPI (lié au compte Windows : la configuration ne peut pas être
déchiffrée depuis un autre compte ou poste). Sous Linux, clé aléatoire locale (`.secret.key`, droits
0600) : cela protège la configuration si elle est copiée seule, pas contre quelqu'un qui accède au compte
de l'utilisateur. Pour transférer la configuration vers un autre poste : **Fichier > Exporter**, avec une
phrase de passe (ou sans les mots de passe), puis **Fichier > Importer**.

## Installation

### Windows : setup.exe

Deux programmes d'installation sont produits dans `dist\` :

| Fichier | Pour |
|---|---|
| `ShellyConfigurator-Setup-1.1.0-x64.exe` | Windows 64 bits (refuse de s'installer sur un Windows 32 bits) |
| `ShellyConfigurator-Setup-1.1.0-x86.exe` | Windows 32 bits (fonctionne aussi sur un Windows 64 bits) |

Sur Windows 7, le poste cible doit avoir le **SP1** et la mise à jour **KB2999226** (runtime C
universel), présente sur tout Windows 7 à jour ; sinon le programme d'installation le signale et s'arrête.

Pour les reconstruire, sur n'importe quel Windows (7 SP1 à 11), il faut :

* **Python 3.8.10** en 64 bits et/ou 32 bits (dernière version compatible Windows 7), accessibles par
  le lanceur `py` (`py -3.8-64`, `py -3.8-32`)
* **Inno Setup 6**

```bat
installer\windows\build_setup.bat
```

construit les deux versions ; `build_setup.bat x64` ou `build_setup.bat x86` n'en construit qu'une.

### Linux

```bash
sudo ./installer/linux/install.sh
```

pour tous les utilisateurs (`/opt/shelly-configurator`), ou sans `sudo` pour l'utilisateur courant. Le
script vérifie Python 3.7+ et Tkinter (et l'installe via apt/dnf/yum/zypper/pacman s'il est lancé avec
`sudo`), crée la commande `shelly-configurator` et l'entrée de menu. Pour distribuer une archive :
`./installer/linux/build_archive.sh`.

### Depuis les sources (les deux systèmes)

```bash
python -m shelly_configurator
```

## Tests

```bash
python -m unittest discover -s tests -t .
```

Les tests utilisent un **faux Shelly** (`tests/fake_shelly.py`) qui simule l'API RPC, l'authentification
digest SHA-256 et les mises à jour firmware. On peut aussi l'utiliser pour essayer l'interface sans
matériel :

```bash
python -m tests.fake_shelly --port 8080 --ver 1.6.2
```

puis utiliser l'adresse `127.0.0.1:8080` dans l'utilitaire.

## Remarques

* Si le PC est connecté au point d'accès du Shelly et que la configuration désactive ce point d'accès,
  la connexion est coupée à la fin : c'est normal, l'outil le signale par un avertissement.
* La mise à jour depuis un fichier nécessite que le pare-feu du PC autorise la connexion entrante du
  Shelly (port choisi automatiquement).
* Le mot de passe administrateur n'est jamais envoyé en clair : seul son hachage (`ha1`) est transmis.
  En revanche, l'API HTTP des Shelly n'est pas chiffrée : les mots de passe Wi-Fi et MQTT transitent en
  clair sur le réseau local au moment de la configuration (limite du firmware Shelly).
