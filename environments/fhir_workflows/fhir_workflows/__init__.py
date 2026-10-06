def load_environment(**kwargs):
    # Keep CLI compilation usable without importing the rollout framework.
    from .fhir_workflows import load_environment as loader

    return loader(**kwargs)


__all__ = ["load_environment"]
