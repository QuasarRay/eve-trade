package rabbitmqsettlement

import (
	"errors"
	"testing"

	"connectrpc.com/connect"
)

func TestConnectCodeRoundTripForEverySupportedCode(t *testing.T) {
	codes := []connect.Code{
		connect.CodeCanceled, connect.CodeInvalidArgument, connect.CodeDeadlineExceeded,
		connect.CodeNotFound, connect.CodeAlreadyExists, connect.CodePermissionDenied,
		connect.CodeResourceExhausted, connect.CodeFailedPrecondition, connect.CodeAborted,
		connect.CodeOutOfRange, connect.CodeUnimplemented, connect.CodeInternal,
		connect.CodeUnavailable, connect.CodeDataLoss, connect.CodeUnauthenticated,
	}
	for _, code := range codes {
		if got := connectCodeFromName("  " + code.String() + "  "); got != code {
			t.Errorf("connectCodeFromName(%q) = %v, want %v", code, got, code)
		}
		if got := connectCodeName(connect.NewError(code, errors.New("failure"))); got != code.String() {
			t.Errorf("connectCodeName(%v) = %q", code, got)
		}
	}
	if got := connectCodeFromName("not-a-code"); got != connect.CodeUnknown {
		t.Fatalf("unknown code = %v, want unknown", got)
	}
}
