const list = document.querySelector('#services');
const count = document.querySelector('#count');
const updated = document.querySelector('#updated');
const notice = document.querySelector('#notice');
const empty = document.querySelector('#empty');
const search = document.querySelector('#search');
const refreshButton = document.querySelector('#refresh');
const filters = document.querySelector('#filters');
const sortSelect = document.querySelector('#sort');
const mapTotal = document.querySelector('#map-total');
const mapWeb = document.querySelector('#map-web');
const mapApi = document.querySelector('#map-api');
const mapOther = document.querySelector('#map-other');
const mapCore = document.querySelector('.signal-core');
const tmuxList = document.querySelector('#tmux-projects');
const toastList = document.querySelector('#toasts');
const tmuxOther = document.querySelector('#tmux-other');
const tmuxDialog = document.querySelector('#tmux-confirm');
const tabServices = document.querySelector('#tab-services');
const tabTmux = document.querySelector('#tab-tmux');
const servicesPanel = document.querySelector('#directory');
const tmuxPanel = document.querySelector('#tmux');
let services = [];
let tmuxProjects = [];
let tmuxJobs = [];
const toasts = new Map();
const dismissedToasts = new Set();
const seenTmuxJobs = new Map();
const jobToastKeys = new Map();
const pendingToastKeys = new Map();
let jobsInitialized = false;
let toastSequence = 0;
const pendingTmuxActions = new Map();
let tmuxToken = null;
let refreshing = false;
let currentVersion = null;
let activeFilter = 'all';
let pendingTmuxCard = location.hash.startsWith('#tmux-') ? location.hash.slice(1) : null;
const labels = {web: 'Web app', api: 'API', http: 'HTTP', tcp: 'TCP'};
const priority = {web: 0, api: 1, http: 2, tcp: 3};
const collator = new Intl.Collator('it', {numeric: true, sensitivity: 'base'});

function element(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content != null) node.textContent = content;
  return node;
}

function formatUptime(startedAt) {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - startedAt));
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor(seconds % 86400 / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  if (days) return `${days}g ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${Math.max(1, minutes)}m`;
}

function updateUptimes() {
  for (const node of document.querySelectorAll('[data-started-at]')) {
    node.textContent = `${node.dataset.prefix} · attivo da ${formatUptime(Number(node.dataset.startedAt))}`;
  }
}

function row(service, index) {
  const root = element('article', `service-row ${service.kind}${service.protocol ? ' detected' : ''}`);
  root.append(element('span', 'row-index', String(index + 1).padStart(2, '0')));

  const identity = element('div', 'row-identity');
  const iconBox = element('div', 'row-icon');
  if (service.favicon) {
    const icon = element('img');
    icon.src = service.favicon;
    icon.alt = '';
    icon.addEventListener('error', () => {
      iconBox.replaceChildren(document.createTextNode(service.kind === 'api' ? '{}' : '✳'));
    });
    iconBox.append(icon);
  } else {
    iconBox.textContent = service.kind === 'api' ? '{}' : service.protocol ? '▤' : service.kind === 'tcp' ? '•' : '✳';
  }
  const rowText = element('div', 'row-text');
  const launcher = tmuxProjects.find(project => project.session === service.tmux?.session);
  rowText.append(element('span', 'row-kind',
    `${service.protocol ? 'SERVIZIO TCP' : labels[service.kind] || 'HTTP'}${service.docker ? ' · DOCKER' : ''}${service.source === 'windows' ? ' · WINDOWS' : ''}`));
  rowText.append(element('h3', 'row-title', service.protocol || service.title || service.project || service.docker?.name || service.command || `Porta ${service.port}`));
  const details = [];
  if (service.protocol) details.push({handshake: 'Protocollo verificato', process: 'Riconosciuto dal processo', image: 'Probabile dall’immagine Docker', port: 'Probabile dalla porta'}[service.protocol_evidence]);
  if (service.docker) details.push(service.docker.name);
  if (service.docker?.compose_project) details.push(service.docker.compose_project);
  if (service.protocol && service.project) details.push(service.project);
  if (service.title && service.project) details.push(service.project);
  if (service.tmux) details.push(`${service.tmux.session} / ${service.tmux.window}`);
  if (launcher) details.push(`${launcher.id}.sh`);
  else if (service.command) details.push(service.command);
  rowText.append(element('div', 'row-sub', details.join(' · ') || 'Processo non disponibile'));
  if (service.docker?.internal_port) {
    rowText.append(element('span', 'row-mapping',
      `${service.host}:${service.port} → container:${service.docker.internal_port}/tcp`));
  }
  const startedAt = service.started_at || service.docker?.started_at;
  const pid = service.pid;
  if (startedAt || pid) {
    const prefix = service.pid ? `PID ${service.pid}` : 'Container Docker';
    const runtime = element('time', 'row-uptime', prefix);
    if (startedAt) {
      runtime.dataset.startedAt = String(startedAt);
      runtime.dataset.prefix = prefix;
      runtime.dateTime = new Date(startedAt * 1000).toISOString();
      runtime.title = `Avviato il ${new Date(startedAt * 1000).toLocaleString('it-IT')}`;
    }
    rowText.append(runtime);
  }
  identity.append(iconBox, rowText);
  root.append(identity);

  const context = element('div', 'row-context');
  context.append(element('span', '', service.tmux ? 'SESSIONE TMUX' : service.docker ? 'CONTAINER DOCKER' : 'PROCESSO'));
  context.append(element('strong', '', service.tmux
    ? `${service.tmux.session} / ${service.tmux.window}` : service.docker?.name || service.command || 'Non disponibile'));
  context.append(element('small', '', service.docker ? `${service.docker.image || ''} · ${service.host}` : service.host));
  if (launcher) {
    const link = element('a', 'tmux-link', `↗ ${launcher.id}.sh`);
    link.href = `#tmux-${launcher.id}`;
    link.addEventListener('click', () => {
      activateTab('tmux');
      requestAnimationFrame(() => document.getElementById(`tmux-${launcher.id}`)?.scrollIntoView({block: 'start'}));
    });
    context.append(link);
  }
  root.append(context);

  const port = element('div', 'row-port');
  port.append(element('small', '', 'PORTA'), element('strong', '', String(service.port)));
  root.append(port);

  const action = service.url ? element('a', 'row-action', '↗') : element('span', 'row-action inactive', '—');
  if (service.url) {
    action.href = service.url;
    action.target = '_blank';
    action.rel = 'noopener noreferrer';
    action.title = service.kind === 'web' ? 'Apri applicazione' : 'Apri endpoint HTTP';
    action.setAttribute('aria-label', `${action.title}: ${service.title || service.project || service.port}`);
  }
  root.append(action);
  return root;
}

function renderMap() {
  const web = services.filter(service => service.kind === 'web').length;
  const api = services.filter(service => service.kind === 'api').length;
  const other = services.length - web - api;
  mapTotal.textContent = String(services.length).padStart(2, '0');
  mapWeb.textContent = String(web).padStart(2, '0');
  mapApi.textContent = String(api).padStart(2, '0');
  mapOther.textContent = String(other).padStart(2, '0');
  mapCore.style.setProperty('--web-angle', `${services.length ? 360 * web / services.length : 0}deg`);
  mapCore.style.setProperty('--api-angle', `${services.length ? 360 * (web + api) / services.length : 0}deg`);
  mapCore.classList.toggle('is-empty', services.length === 0);
  for (const button of document.querySelectorAll('[data-map-filter]')) {
    button.setAttribute('aria-pressed', String(button.dataset.mapFilter === activeFilter));
  }
}

function render() {
  const query = search.value.trim().toLocaleLowerCase();
  const shown = services.filter(service => {
    const matchesType = activeFilter === 'all' || service.kind === activeFilter
      || (activeFilter === 'other' && ['http', 'tcp'].includes(service.kind));
    return matchesType && [service.title, service.project, service.command, service.protocol, service.port,
      service.docker?.name, service.docker?.image, service.docker?.compose_project,
      service.host, service.tmux?.session, service.tmux?.window]
      .some(value => String(value ?? '').toLocaleLowerCase().includes(query));
  });
  const name = service => service.protocol || service.title || service.project || service.docker?.name || service.command || `Porta ${service.port}`;
  const sort = sortSelect.value;
  shown.sort((a, b) => {
    if (sort === 'port-asc') return a.port - b.port;
    if (sort === 'port-desc') return b.port - a.port;
    if (sort === 'name-asc') return collator.compare(name(a), name(b)) || a.port - b.port;
    if (sort === 'name-desc') return collator.compare(name(b), name(a)) || a.port - b.port;
    return (priority[a.kind] ?? 9) - (priority[b.kind] ?? 9) || a.port - b.port;
  });
  count.textContent = String(services.length);
  renderMap();
  for (const button of filters.querySelectorAll('button[data-filter]')) {
    const filter = button.dataset.filter;
    const total = services.filter(service => filter === 'all' || service.kind === filter
      || (filter === 'other' && ['http', 'tcp'].includes(service.kind))).length;
    button.querySelector('.filter-count').textContent = String(total);
    button.setAttribute('aria-pressed', String(filter === activeFilter));
  }
  list.replaceChildren(...shown.map(row));
  empty.hidden = shown.length > 0;
  if (!shown.length) {
    empty.replaceChildren(element('strong', '', query ? 'Nessun risultato' : 'Nessun servizio trovato'),
      document.createTextNode(query ? 'Prova un altro nome o una porta.'
        : activeFilter !== 'all' ? 'Nessun servizio di questo tipo è attivo.'
          : 'Avvia un server locale e apparirà qui automaticamente.'));
  }
}

function showUpdated(timestamp) {
  updated.textContent = new Date(timestamp * 1000).toLocaleTimeString('it-IT',
    {hour: '2-digit', minute: '2-digit', second: '2-digit'});
}

function applySnapshot(data) {
  if (currentVersion !== null && data.version !== currentVersion) {
    window.location.reload();
    return;
  }
  currentVersion = data.version;
  services = data.services;
  tmuxProjects = data.projects;
  tmuxJobs = data.jobs;
  tmuxToken = data.token;
  showUpdated(data.updated_at);
  const errors = [];
  if (data.listener_error) errors.push(`Rilevamento porte: ${data.listener_error}`);
  if (data.tmux_error) errors.push(`Tmux: ${data.tmux_error}`);
  if (data.docker_error) errors.push(`Docker: ${data.docker_error}`);
  if (data.windows_error) errors.push(`Windows: ${data.windows_error}`);
  notice.hidden = errors.length === 0;
  notice.textContent = errors.join(' · ');
  tmuxOther.hidden = data.other_sessions.length === 0;
  tmuxOther.textContent = data.other_sessions.length ? `Altre sessioni attive: ${data.other_sessions.join(' · ')}` : '';
  syncTmuxToasts();
  renderTmux();
  render();
  updateUptimes();
  refreshButton.disabled = refreshing;
}

function renderTmux() {
  document.querySelector('#tmux-count').textContent = tmuxProjects.length;
  tmuxList.replaceChildren(...tmuxProjects.map(project => {
    const card = element('article', `tmux-card ${project.active ? 'active' : ''}`);
    card.id = `tmux-${project.id}`;
    const heading = element('div', 'tmux-card-head');
    const name = element('div');
    name.append(element('span', 'tmux-script', `${project.id}.sh`),
      element('h3', '', project.id.replaceAll('-', ' ')));
    heading.append(name, element('span', 'tmux-state', project.active ? '● ATTIVA' : '○ FERMA'));
    card.append(heading);
    card.append(element('p', 'tmux-card-meta',
      `Sessione ${project.session} · ${project.service_count} servizi rilevati`));
    const actions = element('div', 'tmux-actions');
    if (project.active && project.stop) actions.append(tmuxButton(project, 'stop', 'Ferma sessione'));
    if (!project.active && project.start) actions.append(tmuxButton(project, 'start', 'Avvia sessione'));
    if (!project.active && !project.start) actions.append(element('span', 'tmux-unavailable', 'Avvio da pagina: richiede --detach'));
    for (const action of project.actions) {
      if (project.active) actions.append(tmuxButton(project, action,
        action === 'status' ? 'Stato provider' : `Usa ${action}`));
    }
    card.append(actions);
    return card;
  }));
  scrollToTmuxCard();
}

function closeToast(key) {
  const toast = toasts.get(key);
  if (toast) {
    clearTimeout(toast.timer);
    toast.node.remove();
    toasts.delete(key);
  }
  dismissedToasts.add(key);
}

function showToast(key, title, state, output = '') {
  if (dismissedToasts.has(key)) return;
  let toast = toasts.get(key);
  if (!toast) {
    const node = element('article', 'toast');
    const close = element('button', 'toast-close', '×');
    close.type = 'button';
    close.setAttribute('aria-label', 'Chiudi notifica');
    close.addEventListener('click', () => closeToast(key));
    const heading = element('strong');
    const details = element('pre');
    node.append(close, heading, details);
    toast = {node, heading, details, timer: null};
    toasts.set(key, toast);
    toastList.prepend(node);
  }
  clearTimeout(toast.timer);
  toast.node.className = `toast ${state}`;
  toast.heading.textContent = title;
  toast.details.textContent = output;
  toast.details.hidden = !output;
  if (state !== 'running') toast.timer = setTimeout(() => closeToast(key), 25000);
}

function tmuxActionMessage(action, state) {
  if (state === 'failed') return action === 'start' ? 'Avvio non riuscito'
    : action === 'stop' ? 'Arresto non riuscito' : `${action}: errore`;
  if (action === 'start') return state === 'running' ? 'Avvio in corso…' : 'Avvio completato';
  if (action === 'stop') return state === 'running' ? 'Arresto in corso…' : 'Arresto completato';
  return `${action}: ${state === 'running' ? 'in corso…' : 'completato'}`;
}

function syncTmuxToasts() {
  for (const job of tmuxJobs) {
    const previous = seenTmuxJobs.get(job.id);
    seenTmuxJobs.set(job.id, job.state);
    if (previous === job.state) continue;
    // Al primo caricamento mostra solo i comandi ancora in corso.
    if (!jobsInitialized && job.state !== 'running') continue;
    let key = jobToastKeys.get(job.id);
    if (!key) {
      key = pendingToastKeys.get(job.project) || `job:${job.id}`;
      jobToastKeys.set(job.id, key);
    }
    showToast(key, `${job.project} · ${tmuxActionMessage(job.action, job.state)}`, job.state, job.output);
  }
  jobsInitialized = true;
}

function scrollToTmuxCard() {
  if (!pendingTmuxCard || tmuxPanel.hidden) return;
  const card = document.getElementById(pendingTmuxCard);
  if (!card) return;
  requestAnimationFrame(() => card.scrollIntoView({block: 'start'}));
  pendingTmuxCard = null;
}

function activateTab(view) {
  const tmux = view === 'tmux';
  servicesPanel.hidden = tmux;
  tmuxPanel.hidden = !tmux;
  tabServices.setAttribute('aria-selected', String(!tmux));
  tabTmux.setAttribute('aria-selected', String(tmux));
  tabServices.tabIndex = tmux ? -1 : 0;
  tabTmux.tabIndex = tmux ? 0 : -1;
}

function confirmTmuxAction(project, action) {
  const stopping = action === 'stop';
  const title = stopping ? 'Fermare la sessione?' : `Passare a ${action}?`;
  const description = stopping
    ? `Verrà eseguito lo script di arresto per la sessione ${project.session}. I servizi del progetto potrebbero interrompersi.`
    : `Verrà eseguita l’opzione ${action} dello script ${project.id}. Il cambio provider può riavviare API e worker.`;
  document.querySelector('#tmux-confirm-title').textContent = title;
  document.querySelector('#tmux-confirm-description').textContent = description;
  document.querySelector('#tmux-confirm-project').textContent = `${project.id} / ${project.session}`;
  document.querySelector('#tmux-confirm-submit').textContent = stopping ? 'Ferma sessione' : `Usa ${action}`;
  tmuxDialog.classList.toggle('is-danger', stopping);
  tmuxDialog.returnValue = 'cancel';
  tmuxDialog.showModal();
  return new Promise(resolve => {
    tmuxDialog.addEventListener('close', () => resolve(tmuxDialog.returnValue === 'confirm'), {once: true});
  });
}

function tmuxButton(project, action, label) {
  const runningAction = pendingTmuxActions.get(project.id)
    || tmuxJobs.find(job => job.project === project.id && job.state === 'running')?.action;
  const busy = runningAction === action;
  const busyLabel = action === 'start' ? 'Avvio in corso…'
    : action === 'stop' ? 'Arresto in corso…' : 'Comando in corso…';
  const button = element('button', action === 'stop' ? 'danger' : '', busy ? busyLabel : label);
  button.type = 'button';
  button.disabled = Boolean(runningAction);
  if (busy) button.setAttribute('aria-busy', 'true');
  button.addEventListener('click', () => runTmuxAction(project, action));
  return button;
}

async function runTmuxAction(project, action) {
  const isBusy = () => pendingTmuxActions.has(project.id)
    || tmuxJobs.some(job => job.project === project.id && job.state === 'running');
  if (isBusy()) return;
  if (action !== 'start' && action !== 'status' && !await confirmTmuxAction(project, action)) return;
  if (isBusy()) return;
  pendingTmuxActions.set(project.id, action);
  const toastKey = `request:${++toastSequence}`;
  pendingToastKeys.set(project.id, toastKey);
  showToast(toastKey, `${project.id} · ${tmuxActionMessage(action, 'running')}`, 'running');
  renderTmux();
  try {
    const response = await fetch('/api/tmux/action', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Launchpad-Token': tmuxToken},
      body: JSON.stringify({project: project.id, action}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    if (!tmuxJobs.some(job => job.id === data.job.id)) tmuxJobs.push(data.job);
    syncTmuxToasts();
  } catch (error) {
    showToast(toastKey, `${project.id} · ${tmuxActionMessage(action, 'failed')}`, 'failed', error.message);
  } finally {
    pendingTmuxActions.delete(project.id);
    pendingToastKeys.delete(project.id);
    renderTmux();
  }
}

async function requestRefresh() {
  if (refreshing || !tmuxToken) return;
  refreshing = true;
  refreshButton.disabled = true;
  try {
    const response = await fetch('/api/refresh', {
      method: 'POST', headers: {'X-Launchpad-Token': tmuxToken},
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    showUpdated(data.updated_at);
  } catch (error) {
    notice.hidden = false;
    notice.textContent = `Impossibile aggiornare i servizi: ${error.message}`;
  } finally {
    refreshing = false;
    refreshButton.disabled = !tmuxToken;
  }
}

for (const [button, view] of [[tabServices, 'services'], [tabTmux, 'tmux']]) {
  button.addEventListener('click', () => {
    activateTab(view);
    history.replaceState(null, '', view === 'tmux' ? '#tmux' : '#directory');
  });
  button.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const target = event.key === 'Home' ? tabServices
      : event.key === 'End' ? tabTmux
        : button === tabServices ? tabTmux : tabServices;
    target.click();
    target.focus();
  });
}
window.addEventListener('hashchange', () => {
  const tmux = location.hash === '#tmux' || location.hash.startsWith('#tmux-');
  activateTab(tmux ? 'tmux' : 'services');
  pendingTmuxCard = location.hash.startsWith('#tmux-') ? location.hash.slice(1) : null;
  scrollToTmuxCard();
});
activateTab(location.hash === '#tmux' || location.hash.startsWith('#tmux-') ? 'tmux' : 'services');
tmuxDialog.addEventListener('click', event => {
  if (event.target === tmuxDialog) tmuxDialog.close('cancel');
});

search.addEventListener('input', render);
filters.addEventListener('click', event => {
  const button = event.target.closest('button[data-filter]');
  if (!button || !filters.contains(button)) return;
  activeFilter = button.dataset.filter;
  render();
});
document.querySelector('.signal-field').addEventListener('click', event => {
  const button = event.target.closest('button[data-map-filter]');
  if (!button) return;
  activeFilter = button.dataset.mapFilter;
  activateTab('services');
  history.replaceState(null, '', '#directory');
  render();
  document.querySelector('#directory').scrollIntoView({behavior: 'smooth'});
});
sortSelect.addEventListener('change', render);
refreshButton.disabled = true;
refreshButton.addEventListener('click', requestRefresh);
const events = new EventSource('/api/events');
events.addEventListener('snapshot', event => applySnapshot(JSON.parse(event.data)));
events.addEventListener('error', () => {
  notice.hidden = false;
  notice.textContent = 'Connessione live interrotta: riconnessione in corso…';
});
setInterval(updateUptimes, 60000);
