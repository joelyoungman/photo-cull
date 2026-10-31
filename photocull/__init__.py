"""Photo Cull."""
try:  # check HTTPS certificates against the operating system's own trust store (macOS Keychain, Windows): the
    import truststore  # Python uv installs can lack some certificate authorities, which broke update checks and downloads

    truststore.inject_into_ssl()
except ImportError:
    pass


def https_context():
    """An SSL context that trusts what the operating system trusts. Passed explicitly too, because urllib keeps
    whatever context its first request used."""
    import ssl

    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()
