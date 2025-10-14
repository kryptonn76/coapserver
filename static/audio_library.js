/**
 * Audio Library JavaScript - ESP32 OpenThread Interface
 * Manages audio playback, search, categories, and real-time updates
 */

// Global state
let socket = null;
let currentNode = null;
let searchTimeout = null;
let catalogData = null;

// Category metadata
const CATEGORY_METADATA = {
    'alertes_pti': {
        icon: '🚨',
        name: 'Alertes PTI & Urgences',
        description: 'Déclenchements PTI, situations d\'urgence'
    },
    'securite_evacuation': {
        icon: '🚪',
        name: 'Sécurité & Évacuation',
        description: 'Consignes d\'évacuation, issues de secours'
    },
    'navigation_indoor': {
        icon: '🧭',
        name: 'Navigation Indoor',
        description: 'Guidage, orientation, localisation'
    },
    'operations_techniques': {
        icon: '🔧',
        name: 'Opérations Techniques',
        description: 'Maintenance, interventions techniques'
    },
    'temps_unites': {
        icon: '⏱️',
        name: 'Temps & Unités',
        description: 'Durées, distances, mesures'
    },
    'instructions_consignes': {
        icon: '📋',
        name: 'Instructions & Consignes',
        description: 'Procédures, protocoles, directives'
    },
    'systeme_statut': {
        icon: '⚙️',
        name: 'Système & Statut',
        description: 'États système, confirmations, erreurs'
    },
    // Albums musicaux
    '[1999] Californication': {
        icon: '💿',
        name: 'Red Hot Chili Peppers - Californication',
        description: '🎵 Album musical (15 morceaux)'
    },
    'Aphex Twin - Drukqs': {
        icon: '💿',
        name: 'Aphex Twin - Drukqs',
        description: '🎵 Album musical (30 morceaux)'
    },
    'Gotan Project - La Revancha del': {
        icon: '💿',
        name: 'Gotan Project - La Revancha del Tango',
        description: '🎵 Album musical (11 morceaux)'
    },
    'Moby-Essentials': {
        icon: '💿',
        name: 'Moby - Essentials',
        description: '🎵 Album musical (24 morceaux)'
    },
    'Thievery Corporation - 2000 - M': {
        icon: '💿',
        name: 'Thievery Corporation - Mirror Conspiracy',
        description: '🎵 Album musical (15 morceaux)'
    }
};

/**
 * Load available nodes from network topology (only connected nodes)
 */
async function loadNodes() {
    try {
        const response = await fetch('/api/topology');
        const data = await response.json();

        const nodeSelect = document.getElementById('target-node');
        nodeSelect.innerHTML = '<option value="">-- Sélectionner un node --</option>';

        if (data.nodes && data.nodes.length > 0) {
            // Filter nodes that have a name and are active in the network
            const activeNodes = data.nodes.filter(node => node.name && node.rloc16);

            activeNodes.forEach(node => {
                const option = document.createElement('option');
                option.value = node.name;
                option.textContent = `${node.name} (${node.rloc16} - ${node.role})`;
                nodeSelect.appendChild(option);
            });

            // Auto-select first node if available
            if (activeNodes.length === 1) {
                nodeSelect.selectedIndex = 1;
                currentNode = activeNodes[0].name;
                updateStatus(`Node sélectionné: ${currentNode}`);
            }
        } else {
            showError('Aucun node actif dans le réseau Thread');
        }

        nodeSelect.addEventListener('change', function() {
            currentNode = this.value;
            if (currentNode) {
                updateStatus(`Node sélectionné: ${currentNode}`);
            }
        });
    } catch (error) {
        console.error('Failed to load nodes:', error);
        showError('Impossible de charger la topologie du réseau');
    }
}

/**
 * Load instant messages (20 priority messages)
 */
async function loadInstantMessages() {
    try {
        const response = await fetch('/api/audio/instant');
        const data = await response.json();

        if (!data.success) {
            throw new Error(data.error);
        }

        const container = document.getElementById('instant-messages');
        container.innerHTML = '';

        data.messages.forEach(msg => {
            const card = createMessageCard(msg, true);
            container.appendChild(card);
        });

    } catch (error) {
        console.error('Failed to load instant messages:', error);
        document.getElementById('instant-messages').innerHTML =
            '<div class="error">Erreur de chargement</div>';
    }
}

/**
 * Load all categories with messages
 */
async function loadCategories() {
    try {
        const response = await fetch('/api/audio/catalog');
        const data = await response.json();

        if (!data.success) {
            throw new Error(data.error);
        }

        catalogData = data;
        const container = document.getElementById('categories-container');
        container.innerHTML = '';

        // Update statistics
        document.getElementById('total-messages').textContent = data.statistics.total_messages;
        document.getElementById('total-categories').textContent = data.statistics.categories_count;

        // Filter music albums from categories
        const musicAlbums = [];
        const vocalCategories = [];

        Object.keys(data.categories).forEach(categoryKey => {
            const categoryData = data.categories[categoryKey];
            const metadata = CATEGORY_METADATA[categoryKey];

            // Check if it's a music album (has 💿 icon in metadata)
            if (metadata && metadata.icon === '💿') {
                musicAlbums.push({ key: categoryKey, data: categoryData, metadata: metadata });
            } else {
                vocalCategories.push({ key: categoryKey, data: categoryData, metadata: metadata });
            }
        });

        // Create vocal category sections
        vocalCategories.forEach(cat => {
            const section = createCategorySection(cat.key, cat.data, cat.metadata || {
                icon: '📁',
                name: cat.key,
                description: ''
            });
            container.appendChild(section);
        });

        // Load music albums in separate section
        loadMusicAlbums(musicAlbums);

    } catch (error) {
        console.error('Failed to load categories:', error);
        document.getElementById('categories-container').innerHTML =
            '<div class="error">Erreur de chargement du catalogue</div>';
    }
}

/**
 * Load music albums in dedicated section
 */
function loadMusicAlbums(albums) {
    const container = document.getElementById('music-albums');
    container.innerHTML = '';

    albums.forEach(album => {
        const card = document.createElement('div');
        card.className = 'album-card';
        card.dataset.albumKey = album.key;

        card.innerHTML = `
            <div class="album-icon">${album.metadata.icon}</div>
            <div class="album-name">${album.metadata.name}</div>
            <div class="album-count">${album.data.count} morceaux</div>
            <div class="album-actions">
                <button class="btn-play-album" onclick="playAlbum('${album.key}', event)">
                    ▶️ Tout jouer
                </button>
                <button class="btn-show-tracks" onclick="toggleAlbumTracks('${album.key}', event)">
                    📋 Voir morceaux
                </button>
            </div>
            <div class="album-tracks collapsed" id="tracks-${album.key.replace(/[^a-zA-Z0-9]/g, '_')}">
                <div class="tracks-grid"></div>
            </div>
        `;

        // Populate tracks (hidden initially)
        const tracksGrid = card.querySelector('.tracks-grid');
        album.data.messages.forEach(msg => {
            const trackCard = createMessageCard(msg);
            trackCard.classList.add('track-card');
            tracksGrid.appendChild(trackCard);
        });

        container.appendChild(card);
    });
}

/**
 * Play entire album
 */
async function playAlbum(albumKey, event) {
    event.stopPropagation();

    if (!currentNode) {
        showError('Veuillez sélectionner un node');
        return;
    }

    if (!catalogData || !catalogData.categories[albumKey]) {
        showError('Album non trouvé');
        return;
    }

    const album = catalogData.categories[albumKey];
    const firstTrack = album.messages[0];

    if (!firstTrack) {
        showError('Album vide');
        return;
    }

    try {
        // Play first track of album
        const response = await fetch('/api/audio/play', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                node: currentNode,
                message_id: firstTrack.id
            })
        });

        const data = await response.json();

        if (data.success) {
            const metadata = CATEGORY_METADATA[albumKey];
            updateStatus(`🎵 Lecture album: ${metadata.name} sur ${currentNode}`);
        } else {
            throw new Error(data.error);
        }

    } catch (error) {
        console.error('Failed to play album:', error);
        showError(`Erreur lecture album: ${error.message}`);
    }
}

/**
 * Toggle album tracks display
 */
function toggleAlbumTracks(albumKey, event) {
    event.stopPropagation();

    const safeKey = albumKey.replace(/[^a-zA-Z0-9]/g, '_');
    const tracksContainer = document.getElementById(`tracks-${safeKey}`);
    const button = event.target;

    if (tracksContainer.classList.contains('collapsed')) {
        tracksContainer.classList.remove('collapsed');
        button.textContent = '🔼 Masquer morceaux';
    } else {
        tracksContainer.classList.add('collapsed');
        button.textContent = '📋 Voir morceaux';
    }
}

/**
 * Create a message card element
 */
function createMessageCard(msg, isInstant = false) {
    const template = document.getElementById('message-card-template');
    const card = template.content.cloneNode(true).querySelector('.message-card');

    card.dataset.id = msg.id;
    card.dataset.path = msg.path_full;

    if (isInstant) {
        card.classList.add('instant-card');
    }

    card.querySelector('.message-id').textContent = `#${msg.id}`;
    card.querySelector('.message-category').textContent = msg.category;
    card.querySelector('.message-description').textContent = msg.description;
    card.querySelector('.message-path').textContent = msg.filename;

    return card;
}

/**
 * Create a category section
 */
function createCategorySection(categoryKey, categoryData, metadata) {
    const template = document.getElementById('category-template');
    const section = template.content.cloneNode(true).querySelector('.category-section');

    section.dataset.category = categoryKey;
    section.querySelector('.category-icon').textContent = metadata.icon;
    section.querySelector('.category-name').textContent = metadata.name;
    section.querySelector('.category-count').textContent = `(${categoryData.count} messages)`;

    const messagesGrid = section.querySelector('.messages-grid');
    categoryData.messages.forEach(msg => {
        const card = createMessageCard(msg);
        messagesGrid.appendChild(card);
    });

    return section;
}

/**
 * Play a message on the selected node
 */
async function playMessage(button) {
    const card = button.closest('.message-card');
    const messageId = parseInt(card.dataset.id);

    if (!currentNode) {
        showError('Veuillez sélectionner un node cible');
        return;
    }

    try {
        button.disabled = true;
        button.textContent = '⏳ Envoi...';

        const response = await fetch('/api/audio/play', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                node: currentNode,
                message_id: messageId
            })
        });

        const data = await response.json();

        if (data.success) {
            updateStatus(`▶️ Lecture sur ${currentNode}: ${data.message}`);
            button.textContent = '✓ Envoyé';
            setTimeout(() => {
                button.textContent = '▶️ Jouer';
                button.disabled = false;
            }, 2000);
        } else {
            throw new Error(data.error);
        }

    } catch (error) {
        console.error('Failed to play message:', error);
        showError(`Erreur: ${error.message}`);
        button.textContent = '▶️ Jouer';
        button.disabled = false;
    }
}

/**
 * Stop audio playback
 */
async function stopAudio() {
    if (!currentNode) {
        showError('Aucun node sélectionné');
        return;
    }

    try {
        const response = await fetch('/api/audio/stop', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                node: currentNode
            })
        });

        const data = await response.json();

        if (data.success) {
            updateStatus(`⏹️ Lecture arrêtée sur ${currentNode}`);
        } else {
            throw new Error(data.error);
        }

    } catch (error) {
        console.error('Failed to stop audio:', error);
        showError(`Erreur: ${error.message}`);
    }
}

/**
 * Setup search functionality with debounce
 */
function setupSearch() {
    const searchInput = document.getElementById('search-input');
    const resultsContainer = document.getElementById('search-results');

    searchInput.addEventListener('input', function() {
        const query = this.value.trim();

        // Clear previous timeout
        if (searchTimeout) {
            clearTimeout(searchTimeout);
        }

        // Hide results if query is empty
        if (query.length === 0) {
            resultsContainer.classList.add('hidden');
            return;
        }

        // Debounce search (300ms)
        searchTimeout = setTimeout(() => {
            performSearch(query);
        }, 300);
    });
}

/**
 * Perform search and display results
 */
async function performSearch(query) {
    const resultsContainer = document.getElementById('search-results');

    try {
        resultsContainer.innerHTML = '<div class="loading">Recherche...</div>';
        resultsContainer.classList.remove('hidden');

        const response = await fetch(`/api/audio/search?q=${encodeURIComponent(query)}`);
        const data = await response.json();

        if (!data.success) {
            throw new Error(data.error);
        }

        if (data.count === 0) {
            resultsContainer.innerHTML = '<div class="no-results">Aucun résultat trouvé</div>';
            return;
        }

        resultsContainer.innerHTML = `<div class="results-header">🔍 ${data.count} résultat(s) pour "${query}"</div>`;
        const resultsGrid = document.createElement('div');
        resultsGrid.className = 'messages-grid';

        data.results.forEach(msg => {
            const card = createMessageCard(msg);
            resultsGrid.appendChild(card);
        });

        resultsContainer.appendChild(resultsGrid);

    } catch (error) {
        console.error('Search failed:', error);
        resultsContainer.innerHTML = '<div class="error">Erreur de recherche</div>';
    }
}

/**
 * Setup volume control
 */
function setupVolumeControl() {
    const slider = document.getElementById('volume-slider');
    const valueDisplay = document.getElementById('volume-value');

    slider.addEventListener('input', function() {
        valueDisplay.textContent = `${this.value}%`;
    });

    slider.addEventListener('change', async function() {
        if (!currentNode) {
            showError('Veuillez sélectionner un node');
            return;
        }

        try {
            const response = await fetch('/api/audio/volume', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    node: currentNode,
                    volume: parseInt(this.value)
                })
            });

            const data = await response.json();

            if (data.success) {
                updateStatus(`🔊 Volume réglé à ${this.value}% sur ${currentNode}`);
            } else {
                throw new Error(data.error);
            }

        } catch (error) {
            console.error('Failed to set volume:', error);
            showError(`Erreur de réglage du volume: ${error.message}`);
        }
    });
}

/**
 * Toggle category expansion
 */
function toggleCategory(header) {
    const content = header.nextElementSibling;
    const icon = header.querySelector('.toggle-icon');

    content.classList.toggle('collapsed');
    icon.textContent = content.classList.contains('collapsed') ? '▼' : '▲';
}

/**
 * Toggle section expansion (music)
 */
function toggleSection(sectionClass) {
    const section = document.querySelector(`.${sectionClass}`);
    const content = section.querySelector('.section-content');
    const icon = section.querySelector('.toggle-icon');

    section.classList.toggle('collapsed');
    icon.textContent = section.classList.contains('collapsed') ? '▼' : '▲';
}

/**
 * Connect to WebSocket for real-time updates
 */
function connectWebSocket() {
    try {
        socket = io.connect(location.protocol + '//' + document.domain + ':' + location.port);

        socket.on('connect', function() {
            console.log('WebSocket connected');
        });

        socket.on('audio_playback', function(data) {
            updateStatus(`▶️ ${data.node}: ${data.description}`);
        });

        socket.on('node_update', function(data) {
            // Reload node list if nodes change
            loadNodes();
        });

        socket.on('topology_update', function(data) {
            // Reload nodes when topology is refreshed
            console.log('Topology updated, reloading nodes...');
            loadNodes();
        });

        socket.on('disconnect', function() {
            console.log('WebSocket disconnected');
        });

    } catch (error) {
        console.error('WebSocket connection failed:', error);
    }
}

/**
 * Update status display
 */
function updateStatus(message) {
    const statusBox = document.getElementById('playback-status');
    const statusText = document.getElementById('status-text');

    statusText.textContent = message;
    statusBox.classList.remove('hidden');

    // Auto-hide after 5 seconds
    setTimeout(() => {
        statusBox.classList.add('hidden');
    }, 5000);
}

/**
 * Show error message
 */
function showError(message) {
    const statusBox = document.getElementById('playback-status');
    const statusText = document.getElementById('status-text');
    const statusIcon = statusBox.querySelector('.status-icon');

    statusIcon.textContent = '❌';
    statusText.textContent = message;
    statusBox.classList.remove('hidden');
    statusBox.classList.add('error');

    setTimeout(() => {
        statusBox.classList.add('hidden');
        statusBox.classList.remove('error');
        statusIcon.textContent = '▶️';
    }, 5000);
}
