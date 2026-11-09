const appState = {
    user: null,
    authReady: false,
    currentPlan: null,
    selectedMotorId: null,
};

const viewLogin = document.getElementById('viewLogin');
const viewMainMenu = document.getElementById('viewMainMenu');
const viewImportAutoCAD = document.getElementById('viewImportAutoCAD');
const loginForm = document.getElementById('loginForm');
const menuItemLogout = document.getElementById('menuItemLogout');
const menuItemPlanos = document.getElementById('menuItemPlanos');
const modalSavedPlans = new bootstrap.Modal(document.getElementById('modalSavedPlans'));
const btnGoToImportSection = document.getElementById('btnGoToImportSection');
const btnBackToMainMenu = document.getElementById('btnBackToMainMenu');
const btnImportAutoCAD = document.getElementById('btnImportAutoCAD');
const autocadFileInput = document.getElementById('autocadFileInput');
const excelFileInput = document.getElementById('excelFileInput');
const errorNoPlanAlert = document.getElementById('errorNoPlanAlert');
const loadedPlanCard = document.getElementById('loadedPlanCard');
const planFileName = document.getElementById('planFileName');
const planFileSize = document.getElementById('planFileSize');
const planFileDate = document.getElementById('planFileDate');
const btnRemovePlan = document.getElementById('btnRemovePlan');
const excelLinkedSection = document.getElementById('excelLinkedSection');
const excelFileName = document.getElementById('excelFileName');

function switchView(targetView) {
    if (targetView !== viewLogin && !appState.user) targetView = viewLogin;
    document.querySelectorAll('.app-view').forEach((view) => view.classList.toggle('active', view === targetView));
    document.getElementById('appScreen').scrollTop = 0;
}

function csrfToken() {
    return decodeURIComponent(document.cookie.split('; ').find((entry) => entry.startsWith('csrftoken='))?.slice(10) || '');
}


errorNoPlanAlert.addEventListener('click', () => { errorNoPlanAlert.style.display = 'none'; });
document.getElementById('togglePasswordBtn').addEventListener('click', () => {
    const password = document.getElementById('passwordInput');
    password.type = password.type === 'password' ? 'text' : 'password';
    document.getElementById('togglePasswordIcon').className = password.type === 'password' ? 'bi bi-eye' : 'bi bi-eye-slash';
});
btnGoToImportSection.addEventListener('click', () => {
    if (!appState.user?.permissions.can_view) return;
    errorNoPlanAlert.style.display = 'none';
    switchView(viewImportAutoCAD);
    window.planWorkflow?.activate?.();
});
btnBackToMainMenu.addEventListener('click', () => {
    errorNoPlanAlert.style.display = 'none';
    switchView(viewMainMenu);
});
menuItemPlanos.addEventListener('click', (event) => {
    event.preventDefault();
    if (!appState.user?.permissions.can_view) return;
    modalSavedPlans.show();
    window.planWorkflow.listSaved();
});
