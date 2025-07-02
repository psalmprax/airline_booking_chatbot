// --- DOM Elements ---
const chatMessages = document.getElementById('chat-messages');
const userInput = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');
const historyList = document.getElementById('history-list');
const newChatBtn = document.getElementById('new-chat-btn');
const confirmationModal = document.getElementById('confirmation-modal');
const confirmDeleteBtn = document.getElementById('confirm-delete-btn');
const cancelDeleteBtn = document.getElementById('cancel-delete-btn');
const themeToggle = document.getElementById('theme-toggle');

// --- Configuration & State ---
const RASA_WEBHOOK_URL = 'http://localhost:5005/webhooks/rest/webhook';
let allConversations = [];
let currentConversationId = null;

// --- Core Functions ---

function scrollToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function isUserNearBottom() {
    const scrollThreshold = 50; // pixels
    // Check if the user is scrolled to the bottom before the new message is added.
    return chatMessages.scrollHeight - chatMessages.clientHeight <= chatMessages.scrollTop + scrollThreshold;
}

function saveConversations() {
    localStorage.setItem('airline_bot_conversations', JSON.stringify(allConversations));
}

function generateConversationName(messageText) {
    return messageText.split(' ').slice(0, 4).join(' ') + (messageText.length > 25 ? '...' : '');
}

function saveMessageToHistory(message) {
    const { text, sender } = message;
    const conversation = allConversations.find(c => c.id === currentConversationId);
    if (conversation) {
        conversation.messages.push({ sender, text });
        // If this is the first user message, set the conversation name
        if (sender === 'user' && conversation.messages.filter(m => m.sender === 'user').length === 1) {
            conversation.name = generateConversationName(text);
            renderHistorySidebar(); // Re-render to show the new name
        }
        saveConversations();
    }
}

async function addMessage(message, save = true) {
    const { text, sender } = message;
    const messageElement = document.createElement('div');
    messageElement.classList.add('message', sender === 'user' ? 'user-message' : 'bot-message');

    // Determine if we should scroll after adding the message.
    // We do this BEFORE adding the new element to the DOM.
    // We always scroll for the user's own messages.
    const shouldAutoScroll = save && (isUserNearBottom() || sender === 'user');

    const textSpan = document.createElement('span'); // Create a span for the text

    // Add copy button for ALL bot messages (new and historical)
    if (sender === 'bot') {
        messageElement.appendChild(textSpan); // Add span first

        const copyBtn = document.createElement('button');
        copyBtn.classList.add('copy-btn');
        copyBtn.title = 'Copy text';
        const copyIconSVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor"><path d="M5.5 1.5A1.5 1.5 0 0 0 4 3v8.5A1.5 1.5 0 0 0 5.5 13h5a1.5 1.5 0 0 0 1.5-1.5V3A1.5 1.5 0 0 0 10.5 1.5h-5zM5 3a.5.5 0 0 1 .5-.5h5a.5.5 0 0 1 .5.5v8.5a.5.5 0 0 1-.5.5h-5a.5.5 0 0 1-.5-.5V3z"/><path d="M2 4.5a.5.5 0 0 1 .5-.5H3v8.5A1.5 1.5 0 0 0 4.5 14h5a.5.5 0 0 1 0 1h-5A2.5 2.5 0 0 1 2 12.5V4.5z"/></svg>`;
        const checkIconSVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor"><path fill-rule="evenodd" d="M13.78 4.22a.75.75 0 0 1 0 1.06l-7.25 7.25a.75.75 0 0 1-1.06 0L2.22 9.28a.75.75 0 0 1 1.06-1.06L6 10.94l6.72-6.72a.75.75 0 0 1 1.06 0z"/></svg>`;
        copyBtn.innerHTML = copyIconSVG;

        copyBtn.addEventListener('click', (e) => {
            e.stopPropagation(); // Prevent any other click listeners on the message
            navigator.clipboard.writeText(text).then(() => {
                copyBtn.innerHTML = checkIconSVG;
                copyBtn.title = 'Copied!';
                setTimeout(() => {
                    copyBtn.innerHTML = copyIconSVG;
                    copyBtn.title = 'Copy text';
                }, 2000);
            }).catch(err => {
                console.error('Failed to copy text: ', err);
                copyBtn.title = 'Failed to copy';
            });
        });
        messageElement.appendChild(copyBtn);
    } else {
        // For user messages, just add the text span
        messageElement.appendChild(textSpan);
    }

    // If it's a new bot message, apply the typing effect.
    if (sender === 'bot' && save) {
        messageElement.classList.add('new-message-animation');
        chatMessages.appendChild(messageElement);
        if (shouldAutoScroll) scrollToBottom();

        await new Promise(resolve => {
            let i = 0;
            const typingSpeed = 25; // ms per character

            function type() {
                if (i < text.length) {
                    textSpan.textContent += text.charAt(i);
                    i++;
                    if (shouldAutoScroll) scrollToBottom(); // Keep scrolling as text is added
                    setTimeout(type, typingSpeed);
                } else {
                    resolve();
                }
            }
            textSpan.textContent = ''; // Start with empty text
            type();
        });
        // Save after typing is complete
        saveMessageToHistory(message);
    } else { // For user messages or messages loaded from history
        textSpan.textContent = text; // Set full text instantly
        if (save) { // Only animate new user messages
            messageElement.classList.add('new-message-animation');
        }
        chatMessages.appendChild(messageElement);

        // For historical messages, we don't want to scroll inside this function.
        // For new messages, we scroll based on the check we made earlier.
        if (shouldAutoScroll) {
            scrollToBottom();
        }

        if (save) {
            saveMessageToHistory(message);
        }
    }
}

function addButtons(buttons, save = true) {
    const buttonsContainer = document.createElement('div');
    buttonsContainer.classList.add('buttons-container');

    // Check if we should scroll BEFORE adding the new element
    const shouldAutoScroll = save && isUserNearBottom();

    if (save) { // Only animate new button groups
        buttonsContainer.classList.add('new-message-animation');
    }

    buttons.forEach(button => {
        const buttonElement = document.createElement('button');
        buttonElement.classList.add('message-button');
        buttonElement.innerText = button.title;

        if (save) {
            buttonElement.addEventListener('click', () => {
                addMessage({ text: button.title, sender: 'user' });
                sendMessagePayload(button.payload);
                // Disable all buttons in this group after one is clicked
                buttonsContainer.querySelectorAll('.message-button').forEach(btn => btn.disabled = true);
            });
        } else {
            buttonElement.disabled = true;
        }

        buttonsContainer.appendChild(buttonElement);
    });
    chatMessages.appendChild(buttonsContainer);

    if (shouldAutoScroll) scrollToBottom();

    if (save) {
        const conversation = allConversations.find(c => c.id === currentConversationId);
        if (conversation) {
            // Save the last bot message along with its buttons
            const lastMessage = conversation.messages[conversation.messages.length - 1];
            if (lastMessage && lastMessage.sender === 'bot') {
                lastMessage.buttons = buttons;
                saveConversations();
            }
        }
    }
}

// --- Typing Indicator Functions ---
function showTypingIndicator() {
    // Check if we should scroll BEFORE adding the new element
    const shouldAutoScroll = isUserNearBottom();

    const indicator = document.createElement('div');
    indicator.classList.add('typing-indicator');
    indicator.id = 'typing-indicator';
    indicator.innerHTML = '<span></span><span></span><span></span>';
    indicator.classList.add('new-message-animation');
    chatMessages.appendChild(indicator);
    if (shouldAutoScroll) scrollToBottom();
}

function hideTypingIndicator() {
    const indicator = document.getElementById('typing-indicator');
    if (indicator) {
        indicator.remove();
    }
}

async function handleBotResponses(botResponses) {
    hideTypingIndicator();
    for (const botResponse of botResponses) {
        if (botResponse.text) {
            await addMessage({ text: botResponse.text, sender: 'bot' });
        }
        if (botResponse.buttons && botResponse.buttons.length > 0) {
            addButtons(botResponse.buttons);
        }
    }
}

async function sendMessagePayload(payload) {
    userInput.value = '';
    showTypingIndicator();
    try {
        const response = await fetch(RASA_WEBHOOK_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sender: currentConversationId, message: payload })
        });
        const botResponses = await response.json();
        handleBotResponses(botResponses);
    } catch (error) {
        hideTypingIndicator();
        console.error("Error sending message to Rasa:", error);
        await addMessage({ text: "Sorry, I'm having trouble connecting to the server. Please make sure the bot is running.", sender: 'bot' });
    }
}

async function sendMessage() {
    const messageText = userInput.value.trim();
    if (messageText) {
        addMessage({ text: messageText, sender: 'user' });
        await sendMessagePayload(messageText);
    }
}

// --- History & Conversation Management ---

function performDelete() {
    const idToDelete = confirmationModal.dataset.idToDelete;
    if (!idToDelete) return;
    
    const indexToDelete = allConversations.findIndex(c => c.id === idToDelete);
    if (indexToDelete === -1) return;
    
    // Remove the conversation
    allConversations.splice(indexToDelete, 1);
    saveConversations();
    
    // If the deleted conversation was the active one, decide what to show next
    if (currentConversationId === idToDelete) {
        if (allConversations.length > 0) {
            // Switch to the most recent conversation
            const lastConversationId = allConversations[allConversations.length - 1].id;
            switchConversation(lastConversationId);
        } else {
            // If no conversations are left, create a new one
            createNewConversation();
        }
    } else {
        // If a different conversation was deleted, just re-render the sidebar
        renderHistorySidebar();
    }
}

function deleteConversation(idToDelete, event) {
    event.stopPropagation(); // Prevent the click from triggering switchConversation
    
    // Store the ID on the modal so the confirm button knows what to delete
    confirmationModal.dataset.idToDelete = idToDelete;
    confirmationModal.style.display = 'flex';
}

function startRename(event, conversationId) {
    event.stopPropagation(); // Prevent switching conversation on click

    const listItem = event.target.closest('.history-item');
    if (!listItem) return;

    const nameSpan = listItem.querySelector('.history-item-name');
    const renameBtn = listItem.querySelector('.rename-btn');
    const deleteBtn = listItem.querySelector('.delete-btn');
    if (!nameSpan || !renameBtn || !deleteBtn) return;

    // Hide the static text and buttons
    nameSpan.style.display = 'none';
    renameBtn.style.display = 'none';
    deleteBtn.style.display = 'none';

    // Create and configure the input field
    const input = document.createElement('input');
    input.type = 'text';
    input.classList.add('rename-input');
    input.value = nameSpan.innerText;

    const finishRename = (save) => {
        if (save) {
            const newName = input.value.trim();
            if (newName) {
                const conversation = allConversations.find(c => c.id === conversationId);
                if (conversation) {
                    conversation.name = newName;
                    saveConversations();
                }
            }
        }
        renderHistorySidebar(); // Re-render to restore the original state
    };

    input.addEventListener('blur', () => finishRename(true));
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); finishRename(true); } 
        else if (e.key === 'Escape') { finishRename(false); }
    });

    // Add the input to the DOM and focus it
    listItem.prepend(input);
    input.focus();
    input.select();
}

function renderHistorySidebar() {
    historyList.innerHTML = '';
    // Show most recent first
    [...allConversations].reverse().forEach(conversation => {
        const listItem = document.createElement('li');
        listItem.classList.add('history-item');
        listItem.dataset.id = conversation.id;

        const nameSpan = document.createElement('span');
        nameSpan.classList.add('history-item-name');
        nameSpan.innerText = conversation.name;

        const renameBtn = document.createElement('button');
        renameBtn.classList.add('rename-btn');
        renameBtn.innerHTML = '✏️'; // Pencil emoji
        renameBtn.title = 'Rename chat';
        renameBtn.addEventListener('click', (event) => startRename(event, conversation.id));

        const deleteBtn = document.createElement('button');
        deleteBtn.classList.add('delete-btn');
        deleteBtn.innerHTML = '&times;'; // A simple 'x' character for the delete icon
        deleteBtn.title = 'Delete chat';
        deleteBtn.addEventListener('click', (event) => deleteConversation(conversation.id, event));

        listItem.appendChild(nameSpan);
        listItem.appendChild(renameBtn);
        listItem.appendChild(deleteBtn);

        if (conversation.id === currentConversationId) {
            listItem.classList.add('active');
        }
        listItem.addEventListener('click', () => switchConversation(conversation.id));
        historyList.appendChild(listItem);
    });
}

function clearChatWindow() {
    chatMessages.innerHTML = '';
}

function switchConversation(id) {
    if (id === currentConversationId) return; // Don't switch to the same one
    currentConversationId = id;
    clearChatWindow();
    const conversation = allConversations.find(c => c.id === id);
    if (conversation) {
        conversation.messages.forEach(message => {
            addMessage(message, false); // false: don't re-save
            if (message.buttons) {
                // When loading from history, render all buttons as disabled
                addButtons(message.buttons, false);
            }
        });
    }
    renderHistorySidebar(); // Update active class
    userInput.focus();
    scrollToBottom(); // Scroll to the end of the loaded conversation
}

function createNewConversation() {
    newChatBtn.classList.add('loading');
    newChatBtn.disabled = true;

    // Simulate a short delay for better UX, as the operation is very fast.
    setTimeout(() => {
        const newId = 'user_' + Math.random().toString(36).substr(2, 9);
        const newConversation = {
            id: newId,
            name: "New Chat",
            messages: [{
                text: "Hello! I am your personal airline assistant. How can I help you?",
                sender: 'bot'
            }]
        };
        allConversations.push(newConversation);
        saveConversations();
        switchConversation(newId);

        newChatBtn.classList.remove('loading');
        newChatBtn.disabled = false;
    }, 300); // 300ms delay
}

function setTheme(theme) {
    if (theme === 'dark') {
        document.body.classList.add('dark-mode');
        themeToggle.checked = true;
    } else {
        document.body.classList.remove('dark-mode');
        themeToggle.checked = false;
    }
    // Save the user's preference to localStorage
    localStorage.setItem('airline_bot_theme', theme);
}

function initializeChat() {
    const storedConversations = localStorage.getItem('airline_bot_conversations');
    if (storedConversations) {
        try {
            allConversations = JSON.parse(storedConversations);
        } catch (e) {
            console.error("Error parsing conversations from localStorage", e);
            allConversations = [];
        }
    }

    // --- Theme Initialization ---
    const savedTheme = localStorage.getItem('airline_bot_theme');
    // Check for system preference as a fallback
    const systemPrefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;

    if (savedTheme) {
        setTheme(savedTheme);
    } else if (systemPrefersDark) {
        setTheme('dark');
    } else {
        setTheme('light');
    }

    if (allConversations.length === 0) {
        createNewConversation();
    } else {
        // Switch to the most recent conversation (which is the last one in the array)
        const lastConversationId = allConversations[allConversations.length - 1].id;
        switchConversation(lastConversationId);
    }
}

function hideModal() {
    confirmationModal.style.display = 'none';
    // Clean up the data attribute after the modal is hidden
    delete confirmationModal.dataset.idToDelete;
}

// --- Event Listeners & Initialization ---
sendBtn.addEventListener('click', sendMessage);

userInput.addEventListener('keypress', (event) => {
    if (event.key === 'Enter') {
        sendMessage();
    }
});

newChatBtn.addEventListener('click', createNewConversation);

themeToggle.addEventListener('change', () => {
    if (themeToggle.checked) {
        setTheme('dark');
    } else {
        setTheme('light');
    }
});

confirmDeleteBtn.addEventListener('click', () => {
    performDelete();
    hideModal();
});

cancelDeleteBtn.addEventListener('click', hideModal);

// Also allow closing the modal by clicking the semi-transparent overlay
confirmationModal.addEventListener('click', (event) => {
    // We only hide the modal if the click is on the overlay itself,
    // not on its children (like the modal content box).
    if (event.target === confirmationModal) {
        hideModal();
    }
});

window.addEventListener('load', initializeChat);