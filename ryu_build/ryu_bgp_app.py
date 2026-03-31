from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types
from ryu.services.protocols.bgp.bgpspeaker import BGPSpeaker
import logging

LOG = logging.getLogger('ryu.app.bgp_switch')


class BGPSwitchApp(app_manager.RyuApp):
    """
    Combined OpenFlow 1.3 L2 switch + BGP speaker.
    BGP peers with FRR (172.20.0.2, AS 65001).
    Received BGP prefixes are logged; extend best_path_change_handler
    to program flow rules for inter-VN routing.
    """
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(BGPSwitchApp, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self._start_bgp_speaker()

    # ── BGP Speaker ────────────────────────────────────────────────────────

    def _start_bgp_speaker(self):
        LOG.info("Starting BGP speaker AS=65002 router-id=172.20.0.3")
        self.speaker = BGPSpeaker(
            as_number               = 65002,
            router_id               = "172.20.0.3",
            bgp_server_port         = 179,
            best_path_change_handler= self.best_path_change_handler,
            peer_down_handler       = self.peer_down_handler,
            peer_up_handler         = self.peer_up_handler,
        )

        # Add FRR as eBGP neighbor — Ryu initiates the connection
        self.speaker.neighbor_add(
            address      = "172.20.0.2",
            remote_as    = 65001,
            enable_ipv4  = True,
            connect_mode = "active",
        )
        LOG.info("BGP neighbor added: 172.20.0.2 AS 65001")

    def best_path_change_handler(self, event):
        """Called whenever a BGP best-path is added or withdrawn."""
        if event.is_withdraw:
            LOG.info(f"BGP WITHDRAW   prefix={event.prefix}")
        else:
            LOG.info(
                f"BGP BEST PATH  prefix={event.prefix}"
                f"  nexthop={event.nexthop}"
                f"  remote_as={event.remote_as}"
            )
            # ── Extend here to install OpenFlow forwarding rules ──
            # e.g. self._install_route(event.prefix, event.nexthop)

    def peer_up_handler(self, remote_ip, remote_as):
        LOG.info(f"BGP PEER UP    {remote_ip}  AS {remote_as}")

    def peer_down_handler(self, remote_ip, remote_as):
        LOG.warning(f"BGP PEER DOWN  {remote_ip}  AS {remote_as}")

    # ── OpenFlow L2 Switch ─────────────────────────────────────────────────

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto  = datapath.ofproto
        parser   = datapath.ofproto_parser
        # Table-miss flow: send unmatched packets to controller
        match   = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self._add_flow(datapath, 0, match, actions)
        LOG.info(f"Switch connected: datapath={datapath.id}")

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg      = ev.msg
        datapath = msg.datapath
        ofproto  = datapath.ofproto
        parser   = datapath.ofproto_parser
        in_port  = msg.match["in_port"]

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dst  = eth.dst
        src  = eth.src
        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port

        out_port = self.mac_to_port[dpid].get(dst, ofproto.OFPP_FLOOD)
        actions  = [parser.OFPActionOutput(out_port)]

        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
            self._add_flow(datapath, 1, match, actions)

        data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        out  = parser.OFPPacketOut(
            datapath=datapath, buffer_id=msg.buffer_id,
            in_port=in_port, actions=actions, data=data,
        )
        datapath.send_msg(out)

    def _add_flow(self, datapath, priority, match, actions):
        ofproto = datapath.ofproto
        parser  = datapath.ofproto_parser
        inst    = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod     = parser.OFPFlowMod(
            datapath=datapath, priority=priority,
            match=match, instructions=inst,
        )
        datapath.send_msg(mod)