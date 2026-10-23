FROM nginx:1.28-alpine
COPY --chmod=644 web/gui/nginx.conf /etc/nginx/nginx.conf
COPY --chmod=644 web/gui/index.html web/gui/app.js web/gui/style.css /usr/share/nginx/html/
USER 101:101
ENTRYPOINT ["nginx", "-g", "daemon off;"]
