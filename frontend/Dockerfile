# Use a lightweight nginx image as a base
FROM nginx:alpine

# Remove the default nginx static assets
RUN rm -rf /usr/share/nginx/html/*

# Copy the frontend files from the current directory to the nginx public folder
COPY . /usr/share/nginx/html

# Expose port 80 to the outside world
EXPOSE 80

# The default nginx command will start the server.
# We add '-g "daemon off;"' to run nginx in the foreground, which is best practice for containers.
CMD ["nginx", "-g", "daemon off;"]