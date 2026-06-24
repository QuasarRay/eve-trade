package distributedbackend

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"time"

	"connectrpc.com/connect"
	marketv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/market/v1"
	"google.golang.org/protobuf/encoding/protojson"
)

type QuilkinUDPServer struct {
	addr       string
	maxPacket  int
	timeout    time.Duration
	market     MarketClient
	listenFunc func(network string, address string) (net.PacketConn, error)
}

func NewQuilkinUDPServer(config Config, market MarketClient) *QuilkinUDPServer {
	return &QuilkinUDPServer{
		addr:       config.QuilkinUDPAddr,
		maxPacket:  config.QuilkinMaxPacket,
		timeout:    config.DownstreamTimeout,
		market:     market,
		listenFunc: net.ListenPacket,
	}
}

func (s *QuilkinUDPServer) ListenAndServe(ctx context.Context) error {
	conn, err := s.listenFunc("udp", s.addr)
	if err != nil {
		return fmt.Errorf("listen for Quilkin UDP packets on %s: %w", s.addr, err)
	}
	defer func() {
		if closeErr := conn.Close(); closeErr != nil {
			slog.Warn("quilkin udp close failed", "error", closeErr)
		}
	}()

	go func() {
		<-ctx.Done()
		_ = conn.Close()
	}()

	buffer := make([]byte, s.maxPacket)
	for {
		n, remote, err := conn.ReadFrom(buffer)
		if err != nil {
			if ctx.Err() != nil || errors.Is(err, net.ErrClosed) {
				return nil
			}
			return fmt.Errorf("read Quilkin UDP packet: %w", err)
		}
		packet := append([]byte(nil), buffer[:n]...)
		go s.handlePacket(ctx, conn, remote, packet)
	}
}

func (s *QuilkinUDPServer) handlePacket(parent context.Context, conn net.PacketConn, remote net.Addr, packet []byte) {
	if len(packet) == 0 {
		s.writeError(conn, remote, connect.CodeInvalidArgument, "empty GUI packet")
		return
	}

	ctx, cancel := context.WithTimeout(parent, s.timeout)
	defer cancel()

	response, err := s.market.SubmitTradeGuiInteraction(ctx, &marketv1.SubmitTradeGuiInteractionRequest{
		SourceTransport: "quilkin_udp",
		SourceAddress:   remote.String(),
		RawPayload:      packet,
	})
	if err != nil {
		s.writeError(conn, remote, connect.CodeOf(err), err.Error())
		return
	}

	body, err := protojson.MarshalOptions{UseProtoNames: true}.Marshal(response)
	if err != nil {
		s.writeError(conn, remote, connect.CodeInternal, fmt.Sprintf("marshal response: %v", err))
		return
	}
	_, _ = conn.WriteTo(body, remote)
}

func (s *QuilkinUDPServer) writeError(conn net.PacketConn, remote net.Addr, code connect.Code, message string) {
	body, _ := json.Marshal(map[string]string{
		"code":    code.String(),
		"message": message,
	})
	_, _ = conn.WriteTo(body, remote)
}
