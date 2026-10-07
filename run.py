from app import create_app

app = create_app()

if __name__ == "__main__":
    from app.security import es_desarrollo

    # Debugger de Werkzeug = ejecucion remota de codigo: solo con
    # FLASK_ENV=development, nunca por defecto.
    app.run(debug=es_desarrollo(), host="0.0.0.0", port=5000)
