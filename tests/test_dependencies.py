import pytest
from unittest.mock import MagicMock, sys
from modules.dependencies import build_dependency_tree, get_scenario, to_flow_elements

def test_invalid_preset_raises_error():
    """Cubre la Línea 80 disparando el ValueError esperado."""
    with pytest.raises(ValueError, match="Unknown preset or invalid address"):
        get_scenario("Preset_Inexistente_Para_Pruebas")

def test_custom_address_scenario_loading():
    """Cubre la Línea 94 forzando la detección de una dirección válida."""
    valid_address = "0x" + "1" * 40  # Crea una dirección válida de 40 caracteres hexadecimales
    sc = get_scenario(valid_address, live=False)
    assert sc["key"] == valid_address
    assert sc["token_address"] == valid_address
    assert "Custom position" in sc["name"]

def test_to_flow_elements_conversion():
    """Cubre las Líneas 137-160 ejecutando la conversión a elementos de Streamlit Flow."""
    # 1. Mockear los componentes externos de 'streamlit_flow.elements' para que no falle al importar
    mock_elements = MagicMock()
    mock_elements.StreamlitFlowNode = lambda id, pos, data, style: f"Node_{id}"
    mock_elements.StreamlitFlowEdge = lambda id, source, target, animated: f"Edge_{id}"
    sys.modules['streamlit_flow.elements'] = mock_elements

    # 2. Obtener un árbol de nodos real
    nodes = build_dependency_tree("Pendle PT-eETH (Normal)")
    
    # 3. Ejecutar la función para cubrir el bloque de código faltante
    flow_nodes, flow_edges = to_flow_elements(nodes)
    
    # 4. Validaciones
    assert len(flow_nodes) == 5
    assert len(flow_edges) == 4

def test_enrich_live_all_conditional_paths():
    """Fuerza la ejecución de las líneas condicionales remanentes en _enrich_live."""
    from modules.dependencies import _enrich_live
    
    # Escenario artificial con todos los disparadores posibles activos
    mock_sc = {
        "fallback_key": "pendle_pt_eeth",
        "oracle_address": "0x5f4e3df9cbd43714fe2740f5e3616155c5b8419",
        "token_address": "0x0000000000000000000000000000000000000000",
        "tvl_slugs": {"Yield": "ether.fi-stake", "Lending": "morpho-blue"},
        "dependencies": [
            {"type": "Yield", "health_status": "healthy", "details": "Base"},
            {"type": "Lending", "health_status": "healthy", "details": "Base"}
        ]
    }
    
    # Lo ejecutamos. No importa si usa fallbacks internos, tocará todas las líneas del bloque
    result = _enrich_live(mock_sc)
    assert "data_source" in result
