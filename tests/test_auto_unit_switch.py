"""Tests for automatic unit switching when accessing restricted processes."""

import re
from unittest.mock import MagicMock, patch, call
import pytest
from sei_cli.client import SEIClient
from sei_cli.models import SystemStatus, Unit


# --- Fixtures: realistic HTML snippets ---

ARVORE_RESTRICTED = '''
<script>
Nos[0] = new infraArvoreNo("PROCESSO","48568435",null,"controlador.php?acao=arvore_visualizar&id_procedimento=48568435","ifrVisualizacao","08810254.000108/2026-71","Pessoal","svg/processo.svg?18","svg/processo.svg?18","svg/processo.svg?18",true,true,"noVisitado",null,"noVisitado","08810254.000108/2026-71");
Nos[0].html = 'Processo aberto somente na unidade <a alt="COMANDO DO POSTO AVANÇADO" title="COMANDO DO POSTO AVANÇADO" class="ancoraSigla">CBM - COBM - CMDO PABM APODI</a>.<br />';
Nos[1] = new infraArvoreNo("DOCUMENTO","48568466","48568435","about:blank","ifrConteudoVisualizacao","Memorando 5 (40182408)","Memorando 5","svg/documento_interno.svg?18","svg/documento_interno.svg?18","svg/documento_interno.svg?18",true,false,null,null,"noVisitado","40182408");
Nos[2] = new infraArvoreNo("DOCUMENTO","48568542","48568435","about:blank","ifrConteudoVisualizacao","Memorando 6 (40182482)","Memorando 6","svg/documento_interno.svg?18","svg/documento_interno.svg?18","svg/documento_interno.svg?18",true,false,null,null,"noVisitado","40182482");
NosAcoes[0] = new infraArvoreAcao("BASE_CONHECIMENTO","BC","48568435","#","ifrVisualizacao","Base","svg/bc.svg?18",true);
NosAcoes[1] = new infraArvoreAcao("UNIDADE_GERADORA","UG48568466","48568466","#",null,"COMANDO DO POSTO AVANÇADO BOMBEIRO MILITAR DE APODI / RN",null,true,"CBM - COBM - CMDO PABM APODI");
NosAcoes[2] = new infraArvoreAcao("UNIDADE_GERADORA","UG48568542","48568542","#",null,"COMANDO DO POSTO AVANÇADO BOMBEIRO MILITAR DE APODI / RN",null,true,"CBM - COBM - CMDO PABM APODI");
</script>
'''

ARVORE_UNRESTRICTED = '''
<script>
Nos[0] = new infraArvoreNo("PROCESSO","48568435",null,"controlador.php?acao=arvore_visualizar&id_procedimento=48568435","ifrVisualizacao","08810254.000108/2026-71","Pessoal","svg/processo.svg?18","svg/processo.svg?18","svg/processo.svg?18",true,true,"noVisitado",null,"noVisitado","08810254.000108/2026-71");
Nos[1] = new infraArvoreNo("DOCUMENTO","48568466","48568435","controlador.php?acao=arvore_visualizar&id_documento=48568466","ifrConteudoVisualizacao","Memorando 5 (40182408)","Memorando 5","svg/documento_interno.svg?18","svg/documento_interno.svg?18","svg/documento_interno.svg?18",true,false,null,null,"noVisitado","40182408");
NosAcoes[0] = new infraArvoreAcao("GERAR_PDF","GP","48568435","controlador.php?acao=procedimento_gerar_pdf&id_procedimento=48568435&arvore=1&infra_hash=abc123","ifrVisualizacao","Gerar PDF","svg/pdf.svg?18",true);
</script>
'''

ARVORE_MIXED_CONTEXTUAL = '''
<script>
Nos[0] = new infraArvoreNo("PROCESSO","99999",null,"controlador.php?acao=arvore_visualizar&id_procedimento=99999&infra_unidade_atual=110008367","ifrVisualizacao","proc","tipo","svg","svg","svg",true,true,null,null,null,"proc");
Nos[1] = new infraArvoreNo("DOCUMENTO","111","99999","controlador.php?acao=arvore_visualizar&id_documento=111&id_procedimento=99999&infra_unidade_atual=110008367","ifrConteudoVisualizacao","Doc acessível","Doc acessível","svg/documento_interno.svg","svg","svg",true,false,null,null,null,"111");
Nos[1].src = 'controlador.php?acao=documento_visualizar&id_documento=111&id_procedimento=99999&infra_unidade_atual=110008367';
Nos[2] = new infraArvoreNo("DOCUMENTO","222","99999","about:blank","ifrConteudoVisualizacao","Doc de outra unidade","Doc de outra unidade","svg/documento_interno.svg","svg","svg",true,false,null,null,null,"222");
NosAcoes[0] = new infraArvoreAcao("UNIDADE_GERADORA","UG222","222","#",null,"UNIDADE AUTORA",null,true,"CBM - UNIDADE AUTORA");
</script>
'''

ARVORE_FORWARDED_CAN_CREATE = '''
<script>
Nos[0] = new infraArvoreNo("PROCESSO","99999",null,"controlador.php?acao=arvore_visualizar&id_procedimento=99999","ifrVisualizacao","proc","tipo","svg","svg","svg",true,true,null,null,null,"proc");
Nos[1] = new infraArvoreNo("DOCUMENTO","111","99999","about:blank","ifrConteudoVisualizacao","Doc CACF","Doc CACF","svg/documento_interno.svg","svg","svg",true,false,null,null,null,"111");
NosAcoes[0] = new infraArvoreAcao("UNIDADE_GERADORA","UG111","111","#",null,"UNIDADE AUTORA",null,true,"CBM - CACF");
</script>
<a href="controlador.php?acao=documento_escolher_tipo&id_procedimento=99999"><img src="svg/documento_incluir.svg?18" title="Incluir Documento" /></a>
'''

ARVORE_MULTI_UNIT = '''
<script>
Nos[0] = new infraArvoreNo("PROCESSO","99999",null,"controlador.php?acao=arvore_visualizar","ifrVisualizacao","proc","tipo","svg","svg","svg",true,true,null,null,null,"proc");
Nos[0].html = 'Processo aberto somente na unidade <a class="ancoraSigla">CBM - DAT - 1º CAT (MOSSORÓ)</a>.<br />';
Nos[1] = new infraArvoreNo("DOCUMENTO","111","99999","about:blank","ifrConteudoVisualizacao","Doc1","Doc1","svg","svg","svg",true,false,null,null,null,"111");
Nos[2] = new infraArvoreNo("DOCUMENTO","222","99999","about:blank","ifrConteudoVisualizacao","Doc2","Doc2","svg","svg","svg",true,false,null,null,null,"222");
NosAcoes[0] = new infraArvoreAcao("UNIDADE_GERADORA","UG111","111","#",null,"1º CAT MOSSORÓ",null,true,"CBM - DAT - 1º CAT (MOSSORÓ)");
NosAcoes[1] = new infraArvoreAcao("UNIDADE_GERADORA","UG222","222","#",null,"PABM APODI",null,true,"CBM - COBM - CMDO PABM APODI");
</script>
'''


class TestDetectUnitRestriction:
    def setup_method(self):
        self.client = SEIClient.__new__(SEIClient)

    def test_detects_restricted_process(self):
        result = self.client._detect_unit_restriction(ARVORE_RESTRICTED)
        assert result == "CBM - COBM - CMDO PABM APODI"

    def test_no_restriction_on_accessible_process(self):
        result = self.client._detect_unit_restriction(ARVORE_UNRESTRICTED)
        assert result is None

    def test_detects_from_multi_unit_process(self):
        result = self.client._detect_unit_restriction(ARVORE_MULTI_UNIT)
        assert result == "CBM - DAT - 1º CAT (MOSSORÓ)"

    def test_forwarded_process_with_create_action_is_not_restricted_by_origin_unit(self):
        result = self.client._detect_unit_restriction(ARVORE_FORWARDED_CAN_CREATE)
        assert result is None


class TestDetectDocumentUnits:
    def setup_method(self):
        self.client = SEIClient.__new__(SEIClient)

    def test_maps_docs_to_units(self):
        result = self.client._detect_document_units(ARVORE_RESTRICTED)
        assert result == {
            "48568466": "CBM - COBM - CMDO PABM APODI",
            "48568542": "CBM - COBM - CMDO PABM APODI",
        }

    def test_multi_unit_docs(self):
        result = self.client._detect_document_units(ARVORE_MULTI_UNIT)
        assert result == {
            "111": "CBM - DAT - 1º CAT (MOSSORÓ)",
            "222": "CBM - COBM - CMDO PABM APODI",
        }

    def test_no_ug_on_unrestricted(self):
        result = self.client._detect_document_units(ARVORE_UNRESTRICTED)
        assert result == {}


class TestAutoUnitSwitch:
    def setup_method(self):
        self.client = SEIClient.__new__(SEIClient)

    @patch.object(SEIClient, 'status')
    def test_no_switch_when_unrestricted(self, mock_status):
        """Should yield None without calling switch_unit."""
        with self.client._auto_unit_switch(ARVORE_UNRESTRICTED) as switched:
            assert switched is None
        mock_status.assert_not_called()

    @patch.object(SEIClient, 'status')
    def test_no_switch_when_current_tree_has_contextual_document_url(self, mock_status):
        """A received mixed process must prefer contextual tree URLs."""
        with self.client._auto_unit_switch(ARVORE_MIXED_CONTEXTUAL) as switched:
            assert switched is None
        mock_status.assert_not_called()

    @patch.object(SEIClient, 'status')
    def test_no_switch_when_forwarded_process_can_create_in_current_unit(self, mock_status):
        """Foreign about:blank documents should not block current-unit actions."""
        with self.client._auto_unit_switch(ARVORE_FORWARDED_CAN_CREATE) as switched:
            assert switched is None
        mock_status.assert_not_called()

    @patch.object(SEIClient, 'switch_unit')
    @patch.object(SEIClient, 'list_units')
    @patch.object(SEIClient, 'status')
    def test_switches_and_restores(self, mock_status, mock_units, mock_switch):
        """Should switch to required unit and restore original after."""
        from sei_cli.models import SystemStatus, Unit
        mock_status.return_value = SystemStatus(
            valid=True,
            unidade_sigla="CBM - DAT - SEC  - 1°SAT/1°CAT",
            unidade_descricao="SECRETARIA 1° SAT",
            usuario="TEST",
            ultimo_acesso="",
        )
        # list_units called by _find_accessible_unit AND _can_switch_to
        mock_units.return_value = [
            Unit(sigla="CBM - COBM - CMDO PABM APODI", descricao="PABM", link="110008367"),
            Unit(sigla="CBM - DAT - SEC  - 1°SAT/1°CAT", descricao="SAT", link="110007086"),
        ]

        with self.client._auto_unit_switch(ARVORE_RESTRICTED) as switched:
            assert switched == "CBM - COBM - CMDO PABM APODI"

        # Should have switched to target and back
        assert mock_switch.call_count == 2
        mock_switch.assert_any_call("CBM - COBM - CMDO PABM APODI")
        mock_switch.assert_any_call("CBM - DAT - SEC  - 1°SAT/1°CAT")

    @patch.object(SEIClient, '_find_open_units_from_history', return_value=[])
    @patch.object(SEIClient, 'list_units')
    @patch.object(SEIClient, 'status')
    def test_yields_none_when_no_access(self, mock_status, mock_units, mock_hist):
        """Should yield None (no switch) if user can't access any relevant unit."""
        from sei_cli.models import SystemStatus, Unit
        mock_status.return_value = SystemStatus(
            valid=True,
            unidade_sigla="SOME OTHER UNIT",
            unidade_descricao="Other",
            usuario="TEST",
            ultimo_acesso="",
        )
        mock_units.return_value = [
            Unit(sigla="SOME OTHER UNIT", descricao="Other", link="999"),
        ]

        with self.client._auto_unit_switch(ARVORE_RESTRICTED) as switched:
            assert switched is None

    @patch.object(SEIClient, 'switch_unit')
    @patch.object(SEIClient, 'list_units')
    @patch.object(SEIClient, 'status')
    def test_no_switch_when_already_on_correct_unit(self, mock_status, mock_units, mock_switch):
        """Should not switch if already on the required unit."""
        from sei_cli.models import SystemStatus
        mock_status.return_value = SystemStatus(
            valid=True,
            unidade_sigla="CBM - COBM - CMDO PABM APODI",
            unidade_descricao="PABM",
            usuario="TEST",
            ultimo_acesso="",
        )

        with self.client._auto_unit_switch(ARVORE_RESTRICTED) as switched:
            assert switched is None

        mock_switch.assert_not_called()

    @patch.object(SEIClient, 'switch_unit')
    @patch.object(SEIClient, 'list_units')
    @patch.object(SEIClient, 'status')
    def test_restores_even_on_exception(self, mock_status, mock_units, mock_switch):
        """Should restore original unit even if an exception occurs inside."""
        from sei_cli.models import SystemStatus, Unit
        mock_status.return_value = SystemStatus(
            valid=True,
            unidade_sigla="CBM - DAT - SEC  - 1°SAT/1°CAT",
            unidade_descricao="SAT",
            usuario="TEST",
            ultimo_acesso="",
        )
        mock_units.return_value = [
            Unit(sigla="CBM - COBM - CMDO PABM APODI", descricao="PABM", link="110008367"),
            Unit(sigla="CBM - DAT - SEC  - 1°SAT/1°CAT", descricao="SAT", link="110007086"),
        ]

        with pytest.raises(ValueError):
            with self.client._auto_unit_switch(ARVORE_RESTRICTED) as switched:
                raise ValueError("test error")

        # Should still have tried to restore
        assert mock_switch.call_count == 2
        mock_switch.assert_any_call("CBM - DAT - SEC  - 1°SAT/1°CAT")

    @patch.object(SEIClient, 'switch_unit')
    @patch.object(SEIClient, 'list_units')
    @patch.object(SEIClient, 'status')
    def test_retries_restore_when_status_still_points_to_other_unit(self, mock_status, mock_units, mock_switch):
        from sei_cli.models import SystemStatus, Unit
        mock_status.side_effect = [
            SystemStatus(
                valid=True,
                unidade_sigla="CBM - DAT - SEC  - 1°SAT/1°CAT",
                unidade_descricao="SAT",
                usuario="TEST",
                ultimo_acesso="",
            ),
            SystemStatus(
                valid=True,
                unidade_sigla="PAD-PDF",
                unidade_descricao="PAD",
                usuario="TEST",
                ultimo_acesso="",
            ),
        ]
        mock_units.return_value = [
            Unit(sigla="CBM - COBM - CMDO PABM APODI", descricao="PABM", link="110008367"),
            Unit(sigla="CBM - DAT - SEC  - 1°SAT/1°CAT", descricao="SAT", link="110007086"),
        ]

        with self.client._auto_unit_switch(ARVORE_RESTRICTED) as switched:
            assert switched == "CBM - COBM - CMDO PABM APODI"

        assert mock_switch.call_count == 3
        assert mock_switch.mock_calls[-2:] == [
            call("CBM - DAT - SEC  - 1°SAT/1°CAT"),
            call("CBM - DAT - SEC  - 1°SAT/1°CAT"),
        ]


class TestSwitchUnit:
    @patch("sei_cli.client.parse_unit_switch_form")
    @patch("sei_cli.client.parse_units_switch_page")
    @patch("sei_cli.client.parse_unit_switch_link")
    def test_noop_when_target_is_current_unit(
        self,
        mock_switch_link,
        mock_units_page,
        mock_switch_form,
    ):
        client = SEIClient.__new__(SEIClient)
        client._fresh_control = MagicMock(return_value="<html></html>")
        client._sei_url = lambda path: f"https://sei.rn.gov.br/sei/{path}"
        client._current_unit_id = "110008367"
        client._control_html = None
        client._menu_links = {}
        client._persist_session = MagicMock()
        client.status = MagicMock(return_value=SystemStatus(valid=True, unidade_sigla="CBM - COBM - CMDO PABM APODI"))
        client._post = MagicMock()

        switch_response = MagicMock()
        switch_response.url = "https://sei.rn.gov.br/sei/controlador.php?acao=infra_trocar_unidade&infra_unidade_atual=110008367"
        switch_response.text = "<html></html>"
        client._get = MagicMock(return_value=switch_response)

        mock_switch_link.return_value = "https://sei.rn.gov.br/sei/controlador.php?acao=infra_trocar_unidade&infra_unidade_atual=110008367"
        mock_units_page.return_value = [
            Unit(sigla="CBM - COBM - CMDO PABM APODI", descricao="PABM", link="110008367"),
        ]
        mock_switch_form.return_value = ("controlador.php", {})

        status = client.switch_unit("CBM - COBM - CMDO PABM APODI")

        assert status.valid is True
        client._post.assert_not_called()
        client.status.assert_called_once()

    @patch("sei_cli.client.parse_menu_links")
    @patch("sei_cli.client.parse_system_status")
    @patch("sei_cli.client.parse_unit_switch_form")
    @patch("sei_cli.client.parse_units_switch_page")
    @patch("sei_cli.client.parse_unit_switch_link")
    def test_returns_post_status_when_control_page_parse_is_invalid(
        self,
        mock_switch_link,
        mock_units_page,
        mock_switch_form,
        mock_parse_status,
        mock_menu_links,
    ):
        client = SEIClient.__new__(SEIClient)
        client._fresh_control = MagicMock(return_value="<html></html>")
        client._sei_url = lambda path: f"https://sei.rn.gov.br/sei/{path}"
        client._current_unit_id = "110006929"
        client._control_html = None
        client._menu_links = {}
        client._persist_session = MagicMock()
        client._ensure_session = MagicMock(return_value="<html><title>Controle de Processos</title></html>")
        client._is_valid_control_html = MagicMock(side_effect=[False, True])

        switch_response = MagicMock()
        switch_response.url = "https://sei.rn.gov.br/sei/controlador.php?acao=infra_trocar_unidade&infra_unidade_atual=110006929"
        switch_response.text = "<html>post ok</html>"
        control_response = MagicMock()
        control_response.text = "<html>control invalid</html>"
        client._get = MagicMock(side_effect=[switch_response, control_response])
        client._post = MagicMock(return_value=switch_response)

        mock_switch_link.return_value = "https://sei.rn.gov.br/sei/controlador.php?acao=infra_trocar_unidade&infra_unidade_atual=110006929"
        mock_units_page.return_value = [
            Unit(sigla="CBM - COBM - CMDO PABM APODI", descricao="PABM", link="110008367"),
        ]
        mock_switch_form.return_value = ("controlador.php", {})
        mock_parse_status.side_effect = [
            SystemStatus(valid=True, unidade_sigla="CBM - COBM - CMDO PABM APODI"),
            SystemStatus(valid=True, unidade_sigla="CBM - COBM - CMDO PABM APODI"),
        ]
        mock_menu_links.return_value = {}

        status = client.switch_unit("CBM - COBM - CMDO PABM APODI")

        assert status.valid is True
        assert status.unidade_sigla == "CBM - COBM - CMDO PABM APODI"


class TestFindAccessibleUnit:
    """Tests for _find_accessible_unit (multi-strategy unit discovery)."""

    def setup_method(self):
        self.client = SEIClient.__new__(SEIClient)

    @patch.object(SEIClient, 'list_units')
    def test_direct_restriction_match(self, mock_units):
        """Strategy 1: direct 'Processo aberto somente na unidade' match."""
        from sei_cli.models import Unit
        mock_units.return_value = [
            Unit(sigla="CBM - COBM - CMDO PABM APODI", descricao="PABM", link="1"),
        ]
        result = self.client._find_accessible_unit(ARVORE_RESTRICTED)
        assert result == "CBM - COBM - CMDO PABM APODI"

    @patch.object(SEIClient, '_find_open_units_from_history', return_value=[])
    @patch.object(SEIClient, 'list_units')
    def test_ug_fallback(self, mock_units, mock_hist):
        """Strategy 3: falls back to UNIDADE_GERADORA siglas."""
        from sei_cli.models import Unit
        # Multi-unit: process-level restriction says '1º CAT' but user has PABM
        mock_units.return_value = [
            Unit(sigla="CBM - COBM - CMDO PABM APODI", descricao="PABM", link="1"),
        ]
        result = self.client._find_accessible_unit(ARVORE_MULTI_UNIT)
        assert result == "CBM - COBM - CMDO PABM APODI"

    @patch.object(SEIClient, '_find_open_units_from_history')
    @patch.object(SEIClient, 'list_units')
    def test_history_fallback(self, mock_units, mock_hist):
        """Strategy 4: falls back to process history when other methods fail."""
        from sei_cli.models import Unit
        mock_units.return_value = [
            Unit(sigla="UNIT-X", descricao="X", link="1"),
        ]
        mock_hist.return_value = ["UNIT-Y", "UNIT-X", "UNIT-Z"]
        # ARVORE_RESTRICTED has about:blank docs but user doesn't have PABM APODI
        result = self.client._find_accessible_unit(ARVORE_RESTRICTED)
        assert result == "UNIT-X"


class TestIsProcessInaccessible:
    def setup_method(self):
        self.client = SEIClient.__new__(SEIClient)

    def test_detects_blank_docs(self):
        assert self.client._is_process_inaccessible(ARVORE_RESTRICTED) is True

    def test_accessible_process(self):
        assert self.client._is_process_inaccessible(ARVORE_UNRESTRICTED) is False

    def test_mixed_process_with_contextual_url_is_accessible(self):
        assert self.client._is_process_inaccessible(ARVORE_MIXED_CONTEXTUAL) is False

    def test_forwarded_process_with_create_action_is_accessible(self):
        assert self.client._is_process_inaccessible(ARVORE_FORWARDED_CAN_CREATE) is False


class TestNavigateToArvore:
    def test_retries_contextual_search_after_partial_direct_tree(self):
        client = SEIClient.__new__(SEIClient)
        client._sei_url = lambda path: f"https://sei.rn.gov.br/sei/{path}"
        client._ensure_session = MagicMock()
        client._navigate_to_process_page = MagicMock(return_value=None)
        client._control_html = None

        page = '<html><iframe name="ifrArvore" src="arvore-direta"></iframe></html>'
        direct_response = MagicMock(text=page)
        direct_tree = MagicMock(text=ARVORE_RESTRICTED)
        contextual_tree = MagicMock(text=ARVORE_MIXED_CONTEXTUAL)
        client._get = MagicMock(side_effect=[direct_response, direct_tree, contextual_tree])
        client.search = MagicMock(return_value=page)

        result = client._navigate_to_arvore("99999")

        assert result == ARVORE_MIXED_CONTEXTUAL
        client.search.assert_called_once_with("99999")
        assert client._get.call_count == 3

    def test_keeps_partial_tree_when_no_contextual_route_exists(self):
        client = SEIClient.__new__(SEIClient)
        client._sei_url = lambda path: f"https://sei.rn.gov.br/sei/{path}"
        client._ensure_session = MagicMock()
        client._navigate_to_process_page = MagicMock(return_value=None)
        client._control_html = None
        page = '<html><iframe name="ifrArvore" src="arvore"></iframe></html>'
        client._get = MagicMock(
            side_effect=[MagicMock(text=page), MagicMock(text=ARVORE_RESTRICTED)]
        )
        client.search = MagicMock(side_effect=RuntimeError("search unavailable"))

        result = client._navigate_to_arvore("99999")

        assert result == ARVORE_RESTRICTED
        client.search.assert_called_once_with("99999")
