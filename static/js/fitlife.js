/**
 * FitLife - Control de Aforo y Simulación de Acceso QR en Vivo
 * Implementa sondeo periódico (polling) y llamadas asíncronas vía Fetch API.
 */

document.addEventListener('DOMContentLoaded', function () {
    // --------------------------------------------------------------------------
    // 1. Sondeo Periódico de Aforo (Polling cada 5 segundos)
    // --------------------------------------------------------------------------
    const aforoContainer = document.getElementById('aforo-widget');

    function actualizarAforo() {
        if (!aforoContainer) return;

        fetch('/api/aforo/', { credentials: 'same-origin' })
            .then(response => {
                if (!response.ok) throw new Error('Error al consultar aforo');
                return response.json();
            })
            .then(data => {
                const elPersonas = document.getElementById('aforo-personas');
                const elMaximo = document.getElementById('aforo-maximo');
                const elPorcentaje = document.getElementById('aforo-porcentaje');
                const elDisponibles = document.getElementById('aforo-disponibles');
                const elHora = document.getElementById('aforo-hora-actualizacion');
                const circleProgress = document.getElementById('aforo-circle-progress');

                if (elPersonas) elPersonas.textContent = data.personas_dentro;
                if (elMaximo) elMaximo.textContent = data.capacidad_maxima;
                if (elPorcentaje) elPorcentaje.textContent = `${data.porcentaje_ocupacion}%`;
                if (elDisponibles) elDisponibles.textContent = data.capacidad_disponible;
                if (elHora) elHora.textContent = data.fecha_hora_actual.split(' ')[1] || '';

                // Actualizar anillo SVG circular
                if (circleProgress) {
                    const circum = 2 * Math.PI * 65; // r=65 -> 408.4
                    const offset = circum - (data.porcentaje_ocupacion / 100) * circum;
                    circleProgress.style.strokeDashoffset = Math.max(offset, 0);
                }
            })
            .catch(err => {
                console.warn('Sondeo de aforo pausado o no disponible:', err);
            });
    }

    // Iniciar polling si el widget de aforo está presente en la vista
    if (aforoContainer) {
        actualizarAforo();
        setInterval(actualizarAforo, 5000); // Frecuencia de 5 segundos claramente indicada
    }

    // --------------------------------------------------------------------------
    // 2. Simulador de Validación de Acceso QR (AJAX POST)
    // --------------------------------------------------------------------------
    const formQR = document.getElementById('form-simulador-qr');
    const inputIdentificador = document.getElementById('qr-identificador-input');
    const resultadoBox = document.getElementById('qr-resultado-box');

    // Función auxiliar para obtener el token CSRF de las cookies de Django
    function getCSRFToken() {
        const name = 'csrftoken';
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }

    window.procesarValidacionQR = function (identificador, tipo) {
        if (!identificador) return;

        if (resultadoBox) {
            resultadoBox.style.display = 'block';
            resultadoBox.innerHTML = `
                <div style="text-align: center; padding: 20px;">
                    <div style="font-weight: 600; color: #0284c7;">Validando en el servidor...</div>
                </div>
            `;
        }

        const payload = {
            identificador: identificador,
            tipo: tipo || 'ENTRADA',
            metodo: 'SIMULACION_QR'
        };

        fetch('/api/acceso/validar/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken()
            },
            body: JSON.stringify(payload)
        })
        .then(res => res.json())
        .then(data => {
            mostrarResultadoAcceso(data);
            actualizarAforo(); // Actualizar inmediatamente el aforo
        })
        .catch(err => {
            if (resultadoBox) {
                resultadoBox.innerHTML = `
                    <div style="background-color: #fee2e2; color: #991b1b; padding: 16px; border-radius: 12px;">
                        <strong>Error de comunicación:</strong> No se pudo conectar al servicio de acceso.
                    </div>
                `;
            }
        });
    };

    if (formQR) {
        formQR.addEventListener('submit', function (e) {
            e.preventDefault();
            const id = inputIdentificador ? inputIdentificador.value.trim() : '';
            const tipo = document.querySelector('input[name="tipo_acceso"]:checked')?.value || 'ENTRADA';
            window.procesarValidacionQR(id, tipo);
        });
    }

    function escaparTexto(valor) {
        const nodo = document.createElement('span');
        nodo.textContent = valor == null ? '' : String(valor);
        return nodo.innerHTML;
    }

    function mostrarResultadoAcceso(data) {
        if (!resultadoBox) return;

        const esPermitido = data.autorizado;
        const colorBg = esPermitido ? '#ECFDF5' : '#FEF2F2';
        const colorBorder = esPermitido ? '#10B981' : '#EF4444';
        const colorBadge = esPermitido ? '#065F46' : '#991B1B';
        const bgBadge = esPermitido ? '#D1FAE5' : '#FEE2E2';
        const icono = esPermitido ? '✔' : '✖';
        const titulo = esPermitido ? 'Acceso permitido' : 'Acceso denegado';

        let socioHtml = '';
        if (data.socio) {
            socioHtml = `
                <div style="margin-top: 12px; padding-top: 12px; border-top: 1px solid rgba(0,0,0,0.06); text-align: left;">
                    <div style="font-weight: 800; font-size: 1.1rem; color: #0B192C;">${escaparTexto(data.socio.nombre_completo)}</div>
                    <div style="display: flex; gap: 14px; margin-top: 6px; font-size: 0.85rem; color: #475569; flex-wrap: wrap;">
                        <span><strong>Plan:</strong> ${escaparTexto(data.socio.membresia_plan)}</span>
                        <span><strong>Válida hasta:</strong> ${escaparTexto(data.socio.membresia_valida_hasta)}</span>
                        <span><strong>ID Socio:</strong> #${escaparTexto(data.socio.id)}</span>
                        <span><strong>RUT:</strong> ${escaparTexto(data.socio.rut)}</span>
                    </div>
                </div>
            `;
        }

        resultadoBox.innerHTML = `
            <div style="background-color: ${colorBg}; border: 2px solid ${colorBorder}; border-radius: 14px; padding: 20px; transition: all 0.3s ease;">
                <div style="display: flex; align-items: center; justify-content: space-between;">
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <span style="background-color: ${bgBadge}; color: ${colorBadge}; width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 900;">
                            ${icono}
                        </span>
                        <div>
                            <h4 style="color: ${colorBadge}; font-weight: 800; font-size: 1.15rem; margin: 0;">${titulo}</h4>
                            <p style="color: #334155; font-size: 0.88rem; margin: 4px 0 0 0;">${escaparTexto(data.motivo)}</p>
                        </div>
                    </div>
                    <div style="text-align: right;">
                        <span style="font-family: monospace; font-size: 0.78rem; background: #FFFFFF; padding: 4px 8px; border-radius: 6px; border: 1px solid #CBD5E1; color: #475569;">
                            ⚡ ${escaparTexto(data.tiempo_respuesta_ms)} ms
                        </span>
                    </div>
                </div>
                ${socioHtml}
            </div>
        `;

        // Si existe tabla de últimos accesos en la vista, insertar la fila arriba
        const tablaAccesos = document.getElementById('tabla-ultimos-accesos-body');
        if (tablaAccesos && data.socio) {
            const nuevaFila = document.createElement('tr');
            nuevaFila.innerHTML = `
                <td>${new Date().toLocaleTimeString()}</td>
                <td>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span class="avatar-circle" style="width: 28px; height: 28px; font-size: 0.75rem;">
                            ${escaparTexto(data.socio.nombre_completo.split(' ').map(n=>n[0]).join('').substring(0,2))}
                        </span>
                        <strong>${escaparTexto(data.socio.nombre_completo)}</strong>
                    </div>
                </td>
                <td><span class="badge ${data.tipo === 'ENTRADA' ? 'badge-info' : 'badge-warning'}">${escaparTexto(data.tipo)}</span></td>
                <td><span class="badge ${esPermitido ? 'badge-success' : 'badge-danger'}">${esPermitido ? 'Permitido' : 'Denegado'}</span></td>
            `;
            tablaAccesos.insertBefore(nuevaFila, tablaAccesos.firstChild);
        }
    }
});
