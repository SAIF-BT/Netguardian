const API = "/api";
const TOKEN_KEY = "netguardian.session";
const page = document.body.dataset.page;
const messageBox = document.getElementById("appMessage");
const token = () => localStorage.getItem(TOKEN_KEY);

function showMessage(message, isError = false) {
    if (!messageBox) return;
    messageBox.textContent = message;
    messageBox.classList.toggle("error", isError);
    messageBox.hidden = false;
}

async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body) headers.set("Content-Type", "application/json");
    if (token()) headers.set("Authorization", `Bearer ${token()}`);
    const response = await fetch(`${API}${path}`, { ...options, headers });
    const body = response.status === 204 ? null : await response.json();
    if (!response.ok) {
        const detail = body && body.detail;
        throw new Error(Array.isArray(detail) ? detail.map((item) => item.msg).join(", ") : detail || "Request failed");
    }
    return body;
}

function report(action) {
    return async (event) => {
        event.preventDefault();
        if (messageBox) messageBox.hidden = true;
        try {
            await action(event.currentTarget);
        } catch (error) {
            showMessage(error.message, true);
        }
    };
}

function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
}

function emptyState(container, message) {
    container.replaceChildren(node("p", "empty-state", message));
}

function formatDate(value) {
    return value ? new Date(value).toLocaleString() : "";
}

function setServiceStatus(name, service) {
    const status = document.getElementById(`${name}Status`);
    const dot = document.getElementById(`${name}Dot`);
    if (!status || !dot) return;
    status.textContent = `${service.online ? "Online" : "Offline"} · ${service.message}`;
    dot.classList.toggle("online", service.online);
    dot.classList.toggle("offline", !service.online);
}

async function loadDevices() {
    const result = await api("/devices/");
    return result.devices;
}

async function initializeLogin() {
    const form = document.getElementById("authForm");
    const toggle = document.getElementById("authToggle");
    let registering = false;

    toggle.addEventListener("click", () => {
        registering = !registering;
        document.getElementById("authTitle").textContent = registering ? "Create your account" : "Welcome back";
        document.getElementById("authDescription").textContent = registering
            ? "Create a parent account to set up your prototype."
            : "Sign in to manage your family’s connected experience.";
        document.getElementById("authSubmit").textContent = registering ? "Create account" : "Sign in";
        document.getElementById("authSwitchText").textContent = registering ? "Already have an account?" : "New to NetGuardian?";
        toggle.textContent = registering ? "Sign in" : "Create an account";
        form.elements.password.autocomplete = registering ? "new-password" : "current-password";
    });

    form.addEventListener("submit", report(async (currentForm) => {
        const credentials = Object.fromEntries(new FormData(currentForm));
        if (registering) await api("/auth/register", { method: "POST", body: JSON.stringify(credentials) });
        const session = await api("/auth/login", { method: "POST", body: JSON.stringify(credentials) });
        localStorage.setItem(TOKEN_KEY, session.access_token);
        window.location.assign("dashboard.html");
    }));

    if (token()) {
        try {
            await api("/auth/me");
            window.location.replace("dashboard.html");
        } catch {
            localStorage.removeItem(TOKEN_KEY);
        }
    }
}

async function authorizePage() {
    if (!token()) {
        window.location.replace("index.html");
        return false;
    }
    try {
        await api("/auth/me");
        return true;
    } catch (error) {
        localStorage.removeItem(TOKEN_KEY);
        window.location.replace("index.html");
        return false;
    }
}

function addLogoutHandler() {
    document.querySelectorAll("[data-logout]").forEach((button) => {
        button.addEventListener("click", report(async () => {
            try {
                await api("/auth/logout", { method: "POST" });
            } finally {
                localStorage.removeItem(TOKEN_KEY);
                window.location.assign("index.html");
            }
        }));
    });
}

async function initializeDashboard() {
    const [devices, activity, status, modes] = await Promise.all([
        loadDevices(),
        api("/activity"),
        api("/status"),
        api("/modes"),
    ]);
    document.getElementById("deviceCount").textContent = devices.length;
    document.getElementById("allowedCount").textContent = activity.totals.allowed;
    document.getElementById("blockedCount").textContent = activity.totals.blocked;
    setServiceStatus("hub", status.hub);
    setServiceStatus("esp", status.esp32);

    const picker = document.getElementById("modePicker");
    picker.replaceChildren();
    modes.modes.forEach((mode) => {
        const button = node("button", "mode-button", mode);
        button.type = "button";
        button.setAttribute("aria-pressed", String(mode === modes.active_mode));
        button.addEventListener("click", report(async () => {
            await api("/modes/active", { method: "PUT", body: JSON.stringify({ mode }) });
            await initializeDashboard();
            showMessage(`${mode[0].toUpperCase()}${mode.slice(1)} saved as the default. Active schedules take priority.`);
        }));
        picker.append(button);
    });
    renderActivity(document.getElementById("recentActivity"), activity.events.slice(0, 5));
}

function renderActivity(container, events) {
    if (!events.length) {
        emptyState(container, "No activity yet. DNS and hardware events will appear here.");
        return;
    }
    container.replaceChildren();
    events.forEach((event) => {
        const row = node("article", "activity-row");
        const details = node("div");
        const title = node("strong", "", event.message);
        details.append(title);
        const meta = [event.device_name, event.domain, event.category].filter(Boolean).join(" · ");
        details.append(node("p", "", meta || event.event_type.replaceAll("_", " ")));
        const time = node("time", "activity-meta", formatDate(event.created_at));
        time.dateTime = event.created_at;
        row.append(details, time);
        container.append(row);
    });
}

async function initializeDevices() {
    const form = document.getElementById("deviceForm");
    form.addEventListener("submit", report(async (currentForm) => {
        const data = Object.fromEntries(new FormData(currentForm));
        await api("/devices/", { method: "POST", body: JSON.stringify(data) });
        currentForm.reset();
        await loadDeviceList();
        showMessage("Device registered.");
    }));
    await loadDeviceList();
}

async function loadDeviceList() {
    const [devices, container] = await Promise.all([
        loadDevices(),
        Promise.resolve(document.getElementById("deviceList")),
    ]);
    document.getElementById("deviceListCount").textContent = devices.length;
    if (!devices.length) {
        emptyState(container, "No devices yet. Register the first device above.");
        return devices;
    }
    container.replaceChildren();
    devices.forEach((device) => {
        const row = node("article", "record-row");
        const details = node("div", "record-primary");
        details.append(node("strong", "", device.device_name));
        details.append(node("p", "", `${device.device_type} · ${device.device_identifier} · ${device.is_active ? "Active" : "Paused"}`));
        const actions = node("div", "record-actions");
        const pause = node("button", "button button-secondary", device.is_active ? "Pause" : "Activate");
        pause.type = "button";
        pause.addEventListener("click", report(async () => {
            await api(`/devices/${device.id}/status?is_active=${!device.is_active}`, { method: "PUT" });
            await loadDeviceList();
        }));
        const remove = node("button", "button button-danger", "Remove");
        remove.type = "button";
        remove.addEventListener("click", report(async () => {
            if (!window.confirm(`Remove ${device.device_name}?`)) return;
            await api(`/devices/${device.id}`, { method: "DELETE" });
            await loadDeviceList();
            showMessage("Device removed.");
        }));
        actions.append(pause, remove);
        row.append(details, actions);
        container.append(row);
    });
    return devices;
}

async function initializeRules() {
    const form = document.getElementById("ruleForm");
    form.addEventListener("submit", report(async (currentForm) => {
        const values = Object.fromEntries(new FormData(currentForm));
        const payload = {
            domain: values.domain,
            action: values.action,
            category: values.category,
            mode: values.mode || null,
            device_id: values.device_id ? Number(values.device_id) : null,
        };
        await api("/rules", { method: "POST", body: JSON.stringify(payload) });
        currentForm.reset();
        await loadRuleList();
        showMessage("Website rule saved. The Hub applies it after its next sync.");
    }));
    await loadDeviceOptions(document.getElementById("ruleDevice"));
    await loadRuleList();
}

async function loadDeviceOptions(select) {
    const devices = await loadDevices();
    const keepDefault = select.options[0];
    select.replaceChildren(keepDefault);
    devices.forEach((device) => {
        const option = node("option", "", device.device_name);
        option.value = device.id;
        select.append(option);
    });
}

async function loadRuleList() {
    const { rules } = await api("/rules");
    const container = document.getElementById("ruleList");
    if (!rules.length) {
        emptyState(container, "No website rules saved yet.");
        return;
    }
    container.replaceChildren();
    rules.forEach((rule) => {
        const row = node("article", "record-row");
        const details = node("div", "record-primary");
        details.append(node("strong", "", rule.domain));
        const scope = rule.device_name || "All devices";
        const qualifier = [rule.category, rule.mode, scope].filter(Boolean).join(" · ");
        details.append(node("p", "", qualifier));
        const actions = node("div", "record-actions");
        actions.append(node("span", `badge ${rule.action}`, rule.action));
        const remove = node("button", "button button-danger", "Remove");
        remove.type = "button";
        remove.addEventListener("click", report(async () => {
            await api(`/rules/${rule.id}`, { method: "DELETE" });
            await loadRuleList();
            showMessage("Website rule removed.");
        }));
        actions.append(remove);
        row.append(details, actions);
        container.append(row);
    });
}

async function initializeSchedules() {
    const form = document.getElementById("scheduleForm");
    form.addEventListener("submit", report(async (currentForm) => {
        const values = Object.fromEntries(new FormData(currentForm));
        const payload = {
            device_id: values.device_id ? Number(values.device_id) : null,
            mode: values.mode,
            start_time: values.start_time,
            end_time: values.end_time,
            days: [...currentForm.querySelectorAll('input[name="days"]:checked')].map((input) => input.value),
        };
        await api("/schedules", { method: "POST", body: JSON.stringify(payload) });
        currentForm.reset();
        await loadScheduleList();
        showMessage("Schedule saved. The Hub applies it after its next configuration sync.");
    }));
    await loadDeviceOptions(document.getElementById("scheduleDevice"));
    await loadScheduleList();
}

async function loadScheduleList() {
    const { schedules } = await api("/schedules");
    const container = document.getElementById("scheduleList");
    if (!schedules.length) {
        emptyState(container, "No schedules saved yet.");
        return;
    }
    container.replaceChildren();
    schedules.forEach((schedule) => {
        const row = node("article", "record-row");
        const details = node("div", "record-primary");
        details.append(node("strong", "", `${schedule.mode[0].toUpperCase()}${schedule.mode.slice(1)} · ${schedule.start_time}–${schedule.end_time}`));
        details.append(node("p", "", `${schedule.device_name || "All devices"} · ${schedule.days.join(", ")}`));
        const remove = node("button", "button button-danger", "Remove");
        remove.type = "button";
        remove.addEventListener("click", report(async () => {
            await api(`/schedules/${schedule.id}`, { method: "DELETE" });
            await loadScheduleList();
            showMessage("Schedule removed.");
        }));
        row.append(details, remove);
        container.append(row);
    });
}

async function initializeActivity() {
    document.getElementById("simulateOnline").addEventListener("click", report(async () => {
        await api("/simulator/status", { method: "POST", body: JSON.stringify({ hub_online: true, esp32_online: true }) });
        await loadStatus();
        showMessage("Online state simulated; no physical devices were contacted.");
    }));
    document.getElementById("simulateOffline").addEventListener("click", report(async () => {
        await api("/simulator/status", { method: "POST", body: JSON.stringify({ hub_online: false, esp32_online: false }) });
        await loadStatus();
        showMessage("Offline state simulated; rule enforcement is not active.");
    }));
    document.getElementById("simulateTamper").addEventListener("click", report(async () => {
        await api("/simulator/tamper", { method: "POST", body: JSON.stringify({ opened: true }) });
        await loadStatus();
        await loadEvents();
        showMessage("Tamper event recorded as a simulation. No physical alarm was triggered.");
    }));
    document.getElementById("simulateSecure").addEventListener("click", report(async () => {
        await api("/simulator/tamper", { method: "POST", body: JSON.stringify({ opened: false }) });
        await loadStatus();
        await loadEvents();
        showMessage("Enclosure-secure state recorded as a simulation.");
    }));
    document.getElementById("settingsForm").addEventListener("submit", report(async (form) => {
        const threshold = Number(new FormData(form).get("blocked_attempt_threshold"));
        await api("/settings", { method: "PUT", body: JSON.stringify({ blocked_attempt_threshold: threshold }) });
        showMessage("Alert threshold saved.");
    }));
    document.getElementById("activityForm").addEventListener("submit", report(async (form) => {
        const payload = Object.fromEntries(new FormData(form));
        await api("/simulator/activity", { method: "POST", body: JSON.stringify(payload) });
        form.reset();
        await refreshActivity();
        showMessage("Demo activity recorded.");
    }));
    await Promise.all([loadStatus(), refreshActivity(), loadRequests(), loadSettings()]);
    window.setInterval(() => {
        loadRequests().catch((error) => showMessage(error.message, true));
    }, 5000);
}

async function initializeChildRequest() {
    const form = document.getElementById("childRequestForm");
    form.addEventListener("submit", report(async (currentForm) => {
        const payload = Object.fromEntries(new FormData(currentForm));
        await api("/child/access-requests", {
            method: "POST",
            body: JSON.stringify(payload),
        });
        currentForm.reset();
        showMessage("Request sent to your parent. Ask them to review it in NetGuardian.");
    }));
}

async function loadStatus() {
    const status = await api("/status");
    setServiceStatus("hub", status.hub);
    setServiceStatus("esp", status.esp32);
}

async function loadEvents() {
    const activity = await api("/activity");
    renderActivity(document.getElementById("activityList"), activity.events);
}

async function refreshActivity() {
    const activity = await api("/activity");
    renderActivity(document.getElementById("activityList"), activity.events);
}

async function loadSettings() {
    const settings = await api("/settings");
    document.querySelector('#settingsForm [name="blocked_attempt_threshold"]').value = settings.blocked_attempt_threshold;
}

async function loadRequests() {
    const { requests } = await api("/access-requests");
    const container = document.getElementById("requestList");
    if (!requests.length) {
        emptyState(container, "No access requests yet.");
        return;
    }
    container.replaceChildren();
    requests.forEach((request) => {
        const row = node("article", "record-row");
        const details = node("div", "record-primary");
        details.append(node("strong", "", request.domain));
        details.append(node("p", "", `${request.device_name || "Demo device"} · ${request.reason || "No reason provided"} · ${formatDate(request.created_at)}`));
        const actions = node("div", "record-actions");
        actions.append(node("span", `badge ${request.status}`, request.status));
        if (request.status === "temporary") {
            const expiresAt = request.permission_expires_at
                ? `Expires ${formatDate(request.permission_expires_at)}`
                : "Expiry not available";
            const permission = node("p", "muted", `${expiresAt} · The Hub applies permission after its next sync.`);
            details.append(permission);
        }
        if (request.status === "pending") {
            const allow = node("button", "button button-secondary", "Allow 30 min");
            allow.type = "button";
            allow.addEventListener("click", report(async () => {
                await api(`/access-requests/${request.id}/decision`, {
                    method: "POST",
                    body: JSON.stringify({ decision: "temporary", duration_minutes: 30 }),
                });
                await loadRequests();
                await loadEvents();
                showMessage("Temporary permission approved for 30 minutes. The Hub applies it after its next sync.");
            }));
            const deny = node("button", "button button-danger", "Deny");
            deny.type = "button";
            deny.addEventListener("click", report(async () => {
                await api(`/access-requests/${request.id}/decision`, {
                    method: "POST",
                    body: JSON.stringify({ decision: "deny", duration_minutes: 30 }),
                });
                await loadRequests();
                await loadEvents();
                showMessage("Access request denied.");
            }));
            actions.append(allow, deny);
        }
        row.append(details, actions);
        container.append(row);
    });
}

async function initialize() {
    if (page === "child-request") {
        await initializeChildRequest();
        return;
    }
    if (page === "login") {
        await initializeLogin();
        return;
    }
    if (!await authorizePage()) return;
    addLogoutHandler();
    const initializers = {
        dashboard: initializeDashboard,
        devices: initializeDevices,
        rules: initializeRules,
        schedules: initializeSchedules,
        activity: initializeActivity,
    };
    const initializePage = initializers[page];
    if (initializePage) {
        try {
            await initializePage();
        } catch (error) {
            showMessage(error.message, true);
        }
    }
}

initialize();
