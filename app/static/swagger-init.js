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
    // Pensado para quien llega nuevo: los grupos empiezan plegados para ver el conjunto,
    // «Try it out» ya está activado y se oculta el bloque de esquemas, que es técnico.
    docExpansion: "list",
    defaultModelsExpandDepth: -1,
    tryItOutEnabled: true,
    // El token no se guarda en el navegador: al cerrar la pestaña hay que volver a
    // pulsar Authorize. Así no queda un token válido en localStorage.
    persistAuthorization: false,
  });
});
