// Arranque de Swagger UI. Está en un archivo propio (y no incrustado en el HTML) para
// que la Content-Security-Policy de /docs no tenga que permitir scripts en línea.
window.addEventListener("load", function () {
  window.ui = SwaggerUIBundle({
    url: "/openapi.json",
    dom_id: "#swagger-ui",
    deepLinking: true,
    presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
    layout: "BaseLayout",
    displayRequestDuration: true,
    filter: true,
    // El token no se guarda en el navegador: al cerrar la pestaña hay que volver a
    // pulsar Authorize. Así no queda un token válido en localStorage.
    persistAuthorization: false,
  });
});
