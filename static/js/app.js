// Configuration globale
const socket = io();
let nodes = {};
let demoActive = false;
let eventCount = 0;
let batteryChart = null;
let currentSortMode = 'status';  // Mode de tri par défaut
let trackingMode = false;  // Mode suivi de position
let currentTrackingNode = null;  // Node actuellement active en mode suivi

// Initialisation au chargement de la page
document.addEventListener('DOMContentLoaded', function() {
    console.log('Initialisation de l\'interface IoT Control Center');
    
    // Charger les nodes
    loadNodes();
    
    // Configurer les WebSocket handlers
    setupSocketHandlers();
    
    // Rafraîchir les données périodiquement
    setInterval(loadNodes, 10000); // Toutes les 10 secondes
});

// Chargement des nodes depuis l'API
async function loadNodes() {
    try {
        const response = await fetch('/api/nodes');
        const nodesData = await response.json();
        
        // Mettre à jour les statistiques
        updateStats(nodesData);
        
        // Sauvegarder les données
        nodesData.forEach(nodeData => {
            nodes[nodeData.name] = nodeData;
        });
        
        // Afficher les nodes triés
        displaySortedNodes();
        
    } catch (error) {
        console.error('Erreur lors du chargement des nodes:', error);
    }
}

// Trier et afficher les nodes
function displaySortedNodes() {
    const grid = document.getElementById('nodes-grid');
    const nodesArray = Object.values(nodes);
    
    // Trier selon le mode actuel
    switch(currentSortMode) {
        case 'status':
            // Connectés d'abord, puis par nom
            nodesArray.sort((a, b) => {
                if (a.online !== b.online) {
                    return b.online ? 1 : -1;  // Connectés en premier
                }
                return a.name.localeCompare(b.name);
            });
            break;
            
        case 'ordre':
            // Par ordre de node (0 à la fin)
            nodesArray.sort((a, b) => {
                if (a.ordre === 0 && b.ordre === 0) {
                    return a.name.localeCompare(b.name);
                }
                if (a.ordre === 0) return 1;
                if (b.ordre === 0) return -1;
                return a.ordre - b.ordre;
            });
            break;
            
        case 'name':
            // Alphabétique
            nodesArray.sort((a, b) => a.name.localeCompare(b.name));
            break;
            
        case 'battery':
            // Par niveau de batterie (plus faible en premier)
            nodesArray.sort((a, b) => {
                const battA = a.battery ? a.battery.percentage : 100;
                const battB = b.battery ? b.battery.percentage : 100;
                return battA - battB;
            });
            break;
    }
    
    // Vider la grille
    grid.innerHTML = '';
    
    // Ajouter les cartes dans l'ordre trié
    nodesArray.forEach(nodeData => {
        let card = createNodeCard(nodeData);
        grid.appendChild(card);
    });
}

// Fonction de tri appelée par le select
function sortNodes(sortMode) {
    currentSortMode = sortMode;
    displaySortedNodes();
    
    // Animation de transition
    const cards = document.querySelectorAll('.node-card');
    cards.forEach((card, index) => {
        card.style.animation = 'none';
        setTimeout(() => {
            card.style.animation = `slideIn 0.3s ease ${index * 0.05}s`;
        }, 10);
    });
}

// Créer une carte de node
function createNodeCard(nodeData) {
    const template = document.getElementById('node-card-template');
    const card = template.content.cloneNode(true).querySelector('.node-card');
    
    card.setAttribute('data-node-name', nodeData.name);
    card.querySelector('.node-name').textContent = nodeData.name;
    
    updateNodeCard(card, nodeData);
    
    return card;
}

// Mettre à jour une carte de node
function updateNodeCard(card, nodeData) {
    // Statut en ligne
    const statusIndicator = card.querySelector('.status-indicator');
    if (nodeData.online) {
        statusIndicator.classList.add('online');
        statusIndicator.classList.remove('offline');
    } else {
        statusIndicator.classList.remove('online');
        statusIndicator.classList.add('offline');
    }
    
    // Ordre
    const orderDiv = card.querySelector('.node-order');
    if (nodeData.ordre > 0) {
        orderDiv.textContent = `#${nodeData.ordre}`;
        orderDiv.style.display = 'block';
    } else {
        orderDiv.style.display = 'none';
    }
    
    // Batterie
    if (nodeData.battery) {
        const batteryText = card.querySelector('.battery-text');
        const batteryFill = card.querySelector('.battery-fill');
        const batteryIcon = card.querySelector('.battery-icon');
        
        batteryText.textContent = `${nodeData.battery.voltage.toFixed(2)}V (${nodeData.battery.percentage}%)`;
        batteryFill.style.width = `${nodeData.battery.percentage}%`;
        
        if (nodeData.battery.percentage < 20) {
            batteryFill.classList.add('low');
            batteryIcon.textContent = '🪫';
        } else {
            batteryFill.classList.remove('low');
            batteryIcon.textContent = '🔋';
        }
    }
    
    // LEDs
    const leds = nodeData.leds || {};
    ['red', 'light'].forEach(color => {
        const led = card.querySelector(`.led-${color}`);
        if (led) {
            if (leds[color]) {
                led.classList.add('on');
            } else {
                led.classList.remove('on');
            }
        }
    });
    
    // Adresse IPv6
    const addressDiv = card.querySelector('.node-address');
    addressDiv.textContent = nodeData.address;
    addressDiv.title = `Adresse IPv6: ${nodeData.address}`;
    
    // Dernière activité (sera mise à jour par WebSocket)
    if (!card.dataset.lastActivity) {
        card.querySelector('.activity-time').textContent = 'Jamais';
    }
}

// Mettre à jour les statistiques
function updateStats(nodesData) {
    document.getElementById('total-nodes').textContent = nodesData.length;
    document.getElementById('online-nodes').textContent = nodesData.filter(n => n.online).length;
    document.getElementById('event-count').textContent = eventCount;
}

// Configuration des WebSocket handlers
function setupSocketHandlers() {
    socket.on('connected', function(data) {
        console.log('WebSocket connecté:', data.message);
        addEvent('system', '🔌 Connecté au serveur CoAP');
    });
    
    socket.on('button_event', function(data) {
        console.log('=== BUTTON EVENT RECEIVED ===');
        console.log('Event data:', JSON.stringify(data));
        console.log('Node name from event:', data.node);
        
        eventCount++;
        document.getElementById('event-count').textContent = eventCount;
        
        // Mettre à jour l'état en ligne du node
        if (nodes[data.node]) {
            nodes[data.node].online = true;
            nodes[data.node].lastActivity = new Date().toISOString();
        }
        
        // DEBUG: Lister toutes les cartes disponibles
        const allCards = document.querySelectorAll('.node-card');
        console.log('All available cards:', allCards.length);
        allCards.forEach(c => {
            console.log('  - Card node name:', c.dataset.nodeName);
        });
        
        // Essayer plusieurs sélecteurs
        let card = document.querySelector(`[data-node-name="${data.node}"]`);
        console.log('Card found with data-node-name selector:', card ? 'YES' : 'NO');
        
        if (!card) {
            // Essayer de trouver par texte du nom
            allCards.forEach(c => {
                const nameElement = c.querySelector('.node-name');
                if (nameElement && nameElement.textContent === data.node) {
                    card = c;
                    console.log('Card found by text content:', data.node);
                }
            });
        }
        
        if (card) {
            console.log('CARD FOUND! Applying animation...');
            const now = new Date();
            card.dataset.lastActivity = now.toISOString();
            const activityElement = card.querySelector('.activity-time');
            if (activityElement) {
                activityElement.textContent = formatTime(now);
            }
            
            // Mettre à jour l'indicateur de statut
            const statusIndicator = card.querySelector('.status-indicator');
            if (statusIndicator) {
                statusIndicator.classList.add('online');
                statusIndicator.classList.remove('offline');
            }
            
            // ANIMATION FORCÉE AVEC STYLE INLINE POUR TEST
            console.log('Current classes before:', card.className);
            
            // Retirer les anciennes animations
            card.style.animation = 'none';
            card.classList.remove('button-click', 'button-longpress');
            
            // Forcer le reflow
            void card.offsetWidth;
            
            // Appliquer la nouvelle animation
            if (data.action === 'longpress') {
                console.log('>>> APPLYING LONGPRESS ANIMATION <<<');
                card.style.animation = 'buttonLongPress 1s ease';
                card.classList.add('button-longpress');
                // Effet visuel supplémentaire immédiat
                card.style.border = '2px solid red';
                card.style.boxShadow = '0 0 30px rgba(255, 0, 0, 0.5)';
                
                setTimeout(() => {
                    card.style.animation = '';
                    card.style.border = '';
                    card.style.boxShadow = '';
                    card.classList.remove('button-longpress');
                }, 1000);
            } else {
                console.log('>>> APPLYING CLICK ANIMATION <<<');
                card.style.animation = 'buttonClick 0.6s ease';
                card.classList.add('button-click');
                // Effet visuel supplémentaire immédiat
                card.style.transform = 'scale(1.1)';
                card.style.boxShadow = '0 0 30px rgba(0, 100, 255, 0.5)';
                
                setTimeout(() => {
                    card.style.animation = '';
                    card.style.transform = '';
                    card.style.boxShadow = '';
                    card.classList.remove('button-click');
                }, 600);
            }
            
            console.log('Current classes after:', card.className);
            console.log('Current style.animation:', card.style.animation);
        } else {
            console.error('!!! CARD NOT FOUND FOR NODE:', data.node);
            console.error('Check if node name matches exactly with what is displayed');
        }
        
        // Ajouter l'événement à la liste
        const icon = data.action === 'longpress' ? '🔘🔘' : '🔘';
        const text = data.action === 'longpress' 
            ? `${data.node} - Appui long` 
            : `${data.node} - Clic`;
        addEvent('button', `${icon} ${text}`);
        
        // Re-trier si on trie par statut
        if (currentSortMode === 'status') {
            // Désactiver temporairement le tri pour ne pas perturber l'animation
            setTimeout(() => {
                displaySortedNodes();
            }, 1500);
        }
    });
    
    socket.on('battery_update', function(data) {
        console.log('Mise à jour batterie:', data);
        
        // Mettre à jour les données du node
        if (nodes[data.node]) {
            nodes[data.node].battery = {
                voltage: data.voltage,
                percentage: data.percentage,
                timestamp: data.timestamp
            };
        }
        
        // Mettre à jour la carte
        const card = document.querySelector(`[data-node-name="${data.node}"]`);
        if (card) {
            const batteryText = card.querySelector('.battery-text');
            const batteryFill = card.querySelector('.battery-fill');
            const batteryIcon = card.querySelector('.battery-icon');
            
            batteryText.textContent = `${data.voltage.toFixed(2)}V (${data.percentage}%)`;
            batteryFill.style.width = `${data.percentage}%`;
            
            if (data.low_battery) {
                batteryFill.classList.add('low');
                batteryIcon.textContent = '🪫';
                addEvent('battery', `⚠️ ${data.node} - Batterie faible: ${data.percentage}%`);
            } else {
                batteryFill.classList.remove('low');
                batteryIcon.textContent = '🔋';
            }
        }
        
        // Re-trier si on trie par batterie
        if (currentSortMode === 'battery') {
            displaySortedNodes();
        }
    });
    
    socket.on('led_update', function(data) {
        console.log('Mise à jour LED:', data);
        
        // Mettre à jour l'état visuel de la LED
        const card = document.querySelector(`[data-node-name="${data.node}"]`);
        if (card) {
            const led = card.querySelector(`.led-${data.led}`);
            if (led) {
                if (data.state) {
                    led.classList.add('on');
                } else {
                    led.classList.remove('on');
                }
            }
        }
    });
    
    socket.on('demo_status', function(data) {
        demoActive = data.active;
        const indicator = document.getElementById('demo-indicator');
        if (demoActive) {
            indicator.classList.add('active');
            indicator.querySelector('.stat-value').textContent = '▶️';
            addEvent('system', '🎭 Mode démo activé');
        } else {
            indicator.classList.remove('active');
            indicator.querySelector('.stat-value').textContent = '⏸️';
            addEvent('system', '⏹️ Mode démo désactivé');
        }
    });
    
    socket.on('tracking_mode_status', function(data) {
        trackingMode = data.active;
        const btn = document.getElementById('tracking-mode-btn');
        if (trackingMode) {
            btn.classList.add('active');
            btn.style.backgroundColor = '#e74c3c';
            btn.textContent = '🛑 Arrêter Suivi';
            addEvent('system', '🎯 Mode suivi de position activé');
        } else {
            btn.classList.remove('active');
            btn.style.backgroundColor = '';
            btn.textContent = '🎯 Suivi de Position';
            currentTrackingNode = null;
            addEvent('system', '⏹️ Mode suivi de position désactivé');
            // Retirer les indicateurs visuels
            document.querySelectorAll('.node-card').forEach(card => {
                card.classList.remove('tracking-active', 'tracking-connected');
            });
        }
    });
    
    socket.on('tracking_update', function(data) {
        console.log('Tracking update:', data);
        currentTrackingNode = data.active_node;
        
        // Mettre à jour les cartes visuellement
        document.querySelectorAll('.node-card').forEach(card => {
            const nodeName = card.dataset.nodeName;
            card.classList.remove('tracking-active', 'tracking-connected');
            
            if (nodeName === data.active_node) {
                card.classList.add('tracking-active');
            } else if (data.connected_nodes.includes(nodeName)) {
                card.classList.add('tracking-connected');
            }
        });
        
        addEvent('tracking', `🎯 Détection: ${data.active_node} (connexes: ${data.connected_nodes.join(', ')})`);
    });
}

// Envoyer une commande au serveur
async function sendCommand(type, target, params = {}) {
    try {
        const response = await fetch('/api/command', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                type: type,
                target: target,
                ...params
            })
        });
        
        if (!response.ok) {
            throw new Error('Erreur lors de l\'envoi de la commande');
        }
        
        console.log(`Commande envoyée: ${type} -> ${target}`, params);
    } catch (error) {
        console.error('Erreur:', error);
        addEvent('error', `❌ Erreur: ${error.message}`);
    }
}

// Toggle LED individuelle
function toggleLED(button, ledColor) {
    const card = button.closest('.node-card');
    const nodeName = card.dataset.nodeName;
    const led = card.querySelector(`.led-${ledColor}`);
    const isOn = led.classList.contains('on');
    
    sendCommand('led', nodeName, {
        led: ledColor,
        action: isOn ? 'off' : 'on'
    });
}

// Toggle clignotement
function toggleBlink(button) {
    const card = button.closest('.node-card');
    const nodeName = card.dataset.nodeName;
    const isActive = button.classList.contains('active');
    
    if (isActive) {
        // Arrêter le clignotement
        sendCommand('blink_stop', nodeName);
        button.classList.remove('active');
    } else {
        // Démarrer le clignotement
        const ledSelect = card.querySelector('.blink-led-select');
        const period = card.querySelector('.blink-period');
        const duty = card.querySelector('.blink-duty');
        
        sendCommand('blink', nodeName, {
            led: ledSelect.value,
            period: parseInt(period.value),
            duty: parseInt(duty.value)
        });
        
        button.classList.add('active');
    }
}

// Clignotement global
function startGlobalBlink() {
    const led = document.getElementById('global-blink-led').value;
    const period = document.getElementById('global-blink-period').value;
    const duty = document.getElementById('global-blink-duty').value;
    
    sendCommand('blink', 'all', {
        led: led,
        period: parseInt(period),
        duty: parseInt(duty)
    });
    
    addEvent('control', `💫 Clignotement global démarré: ${led}`);
}

function stopGlobalBlink() {
    sendCommand('blink_stop', 'all');
    
    // Réinitialiser tous les boutons de clignotement
    document.querySelectorAll('.btn-blink').forEach(btn => {
        btn.classList.remove('active');
    });
    
    addEvent('control', '⏹️ Clignotement global arrêté');
}

// Mode démo
function toggleDemo() {
    if (demoActive) {
        sendCommand('demo_stop', 'all');
    } else {
        sendCommand('demo', 'all');
    }
}

// Chemin lumineux
function startLightPath() {
    const speed = prompt('Vitesse du chemin lumineux (ms):', '1000');
    if (speed) {
        sendCommand('path', 'all', {
            speed: parseInt(speed)
        });
        addEvent('control', `🌈 Chemin lumineux démarré (${speed}ms)`);
    }
}

// Flash synchronisé
function flashAll() {
    sendCommand('led', 'all', {led: 'light', action: 'on'});
    setTimeout(() => {
        sendCommand('led', 'all', {led: 'light', action: 'off'});
    }, 1000);
    addEvent('control', '⚡ Flash synchronisé');
}

// Mode suivi de position
function toggleTrackingMode() {
    if (trackingMode) {
        sendCommand('tracking_mode', 'all', {action: 'stop'});
    } else {
        sendCommand('tracking_mode', 'all', {action: 'start'});
    }
}

// Afficher l'historique de batterie
async function showBatteryHistory(button) {
    const card = button.closest('.node-card');
    const nodeName = card.dataset.nodeName;
    
    try {
        const response = await fetch(`/api/battery_history/${nodeName}`);
        const history = await response.json();
        
        if (history.length === 0) {
            alert('Aucun historique de batterie disponible');
            return;
        }
        
        // Préparer les données pour le graphique
        const labels = history.map(h => new Date(h.timestamp).toLocaleTimeString());
        const voltageData = history.map(h => h.voltage);
        const percentageData = history.map(h => h.percentage);
        
        // Afficher le modal
        const modal = document.getElementById('battery-modal');
        document.getElementById('modal-node-name').textContent = `Historique Batterie - ${nodeName}`;
        modal.style.display = 'block';
        
        // Créer ou mettre à jour le graphique
        const ctx = document.getElementById('battery-chart').getContext('2d');
        
        if (batteryChart) {
            batteryChart.destroy();
        }
        
        batteryChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Voltage (V)',
                    data: voltageData,
                    borderColor: 'rgb(75, 192, 192)',
                    backgroundColor: 'rgba(75, 192, 192, 0.2)',
                    yAxisID: 'y',
                }, {
                    label: 'Pourcentage (%)',
                    data: percentageData,
                    borderColor: 'rgb(255, 99, 132)',
                    backgroundColor: 'rgba(255, 99, 132, 0.2)',
                    yAxisID: 'y1',
                }]
            },
            options: {
                responsive: true,
                interaction: {
                    mode: 'index',
                    intersect: false,
                },
                scales: {
                    y: {
                        type: 'linear',
                        display: true,
                        position: 'left',
                        title: {
                            display: true,
                            text: 'Voltage (V)'
                        }
                    },
                    y1: {
                        type: 'linear',
                        display: true,
                        position: 'right',
                        title: {
                            display: true,
                            text: 'Pourcentage (%)'
                        },
                        grid: {
                            drawOnChartArea: false,
                        },
                    },
                }
            }
        });
    } catch (error) {
        console.error('Erreur lors du chargement de l\'historique:', error);
        alert('Erreur lors du chargement de l\'historique');
    }
}

// Fermer le modal
function closeBatteryModal() {
    document.getElementById('battery-modal').style.display = 'none';
    if (batteryChart) {
        batteryChart.destroy();
        batteryChart = null;
    }
}

// Fermer le modal en cliquant en dehors
window.onclick = function(event) {
    const modal = document.getElementById('battery-modal');
    if (event.target == modal) {
        closeBatteryModal();
    }
}

// Ajouter un événement à la liste
function addEvent(type, text) {
    const eventsList = document.getElementById('events-list');
    const eventItem = document.createElement('div');
    eventItem.className = `event-item ${type}-event`;
    
    const now = new Date();
    eventItem.innerHTML = `
        <div class="event-content">
            <span class="event-text">${text}</span>
        </div>
        <span class="event-time">${formatTime(now)}</span>
    `;
    
    // Ajouter en haut de la liste
    eventsList.insertBefore(eventItem, eventsList.firstChild);
    
    // Limiter le nombre d'événements affichés
    while (eventsList.children.length > 50) {
        eventsList.removeChild(eventsList.lastChild);
    }
}

// Formater l'heure
function formatTime(date) {
    const now = new Date();
    const diff = (now - date) / 1000; // différence en secondes
    
    if (diff < 60) {
        return 'À l\'instant';
    } else if (diff < 3600) {
        const minutes = Math.floor(diff / 60);
        return `Il y a ${minutes} min`;
    } else if (diff < 86400) {
        const hours = Math.floor(diff / 3600);
        return `Il y a ${hours}h`;
    } else {
        return date.toLocaleString();
    }
}

// Fonction de test pour les animations (utile pour le debug)
function testAnimation(nodeName, type = 'click') {
    console.log('=== TEST ANIMATION ===');
    console.log('Looking for node:', nodeName);
    
    // Lister toutes les cartes
    const allCards = document.querySelectorAll('.node-card');
    console.log('Available nodes:');
    allCards.forEach(c => {
        console.log('  -', c.dataset.nodeName);
    });
    
    const card = document.querySelector(`[data-node-name="${nodeName}"]`);
    if (card) {
        console.log('Card found! Testing animation:', type);
        
        // Nettoyer
        card.style.animation = 'none';
        card.classList.remove('button-click', 'button-longpress');
        card.style.transform = '';
        card.style.boxShadow = '';
        card.style.border = '';
        
        // Forcer le reflow
        void card.offsetWidth;
        
        // Appliquer l'animation avec styles inline pour garantir la visibilité
        if (type === 'longpress') {
            console.log('Applying LONGPRESS animation with red effect');
            card.style.animation = 'buttonLongPress 1s ease';
            card.style.border = '3px solid red';
            card.style.boxShadow = '0 0 40px rgba(255, 0, 0, 0.7)';
            card.classList.add('button-longpress');
            
            setTimeout(() => {
                card.style.animation = '';
                card.style.border = '';
                card.style.boxShadow = '';
                card.classList.remove('button-longpress');
                console.log('Animation completed');
            }, 1000);
        } else {
            console.log('Applying CLICK animation with blue effect');
            card.style.animation = 'buttonClick 0.6s ease';
            card.style.transform = 'scale(1.2)';
            card.style.boxShadow = '0 0 40px rgba(0, 100, 255, 0.7)';
            card.classList.add('button-click');
            
            setTimeout(() => {
                card.style.animation = '';
                card.style.transform = '';
                card.style.boxShadow = '';
                card.classList.remove('button-click');
                console.log('Animation completed');
            }, 600);
        }
    } else {
        console.error(`Node "${nodeName}" not found!`);
        console.log('Try one of the available nodes listed above');
    }
}

// Fonction pour simuler un événement bouton (pour test)
function simulateButtonEvent(nodeName, action = 'click') {
    const eventData = {
        node: nodeName,
        action: action,
        address: 'test-address',
        timestamp: new Date().toISOString()
    };
    
    console.log('Simulating button event:', eventData);
    
    // Déclencher manuellement le handler
    const handler = socket._callbacks['$button_event'];
    if (handler && handler[0]) {
        handler[0](eventData);
    } else {
        console.error('Socket handler not found, triggering test animation instead');
        testAnimation(nodeName, action);
    }
}

// Animation au chargement
window.addEventListener('load', function() {
    // Animation d'entrée pour les éléments
    const elements = document.querySelectorAll('.header, .global-controls, .nodes-section, .events-section');
    elements.forEach((el, index) => {
        el.style.opacity = '0';
        el.style.transform = 'translateY(20px)';
        setTimeout(() => {
            el.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
            el.style.opacity = '1';
            el.style.transform = 'translateY(0)';
        }, index * 100);
    });
});