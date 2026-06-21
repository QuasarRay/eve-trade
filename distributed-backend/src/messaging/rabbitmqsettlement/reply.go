package rabbitmqsettlement

import (
	"strings"

	"connectrpc.com/connect"
)

type settlementReply struct {
	Success  bool   `json:"success"`
	Code     string `json:"code,omitempty"`
	Error    string `json:"error,omitempty"`
	Response []byte `json:"response,omitempty"`
}

func connectCodeName(err error) string {
	return connect.CodeOf(err).String()
}

func connectCodeFromName(value string) connect.Code {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case connect.CodeCanceled.String():
		return connect.CodeCanceled
	case connect.CodeInvalidArgument.String():
		return connect.CodeInvalidArgument
	case connect.CodeDeadlineExceeded.String():
		return connect.CodeDeadlineExceeded
	case connect.CodeNotFound.String():
		return connect.CodeNotFound
	case connect.CodeAlreadyExists.String():
		return connect.CodeAlreadyExists
	case connect.CodePermissionDenied.String():
		return connect.CodePermissionDenied
	case connect.CodeResourceExhausted.String():
		return connect.CodeResourceExhausted
	case connect.CodeFailedPrecondition.String():
		return connect.CodeFailedPrecondition
	case connect.CodeAborted.String():
		return connect.CodeAborted
	case connect.CodeOutOfRange.String():
		return connect.CodeOutOfRange
	case connect.CodeUnimplemented.String():
		return connect.CodeUnimplemented
	case connect.CodeInternal.String():
		return connect.CodeInternal
	case connect.CodeUnavailable.String():
		return connect.CodeUnavailable
	case connect.CodeDataLoss.String():
		return connect.CodeDataLoss
	case connect.CodeUnauthenticated.String():
		return connect.CodeUnauthenticated
	default:
		return connect.CodeUnknown
	}
}
