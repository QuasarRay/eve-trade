package rabbitmqsettlement

type settlementReply struct {
	Success  bool   `json:"success"`
	Code     string `json:"code,omitempty"`
	Error    string `json:"error,omitempty"`
	Response []byte `json:"response,omitempty"`
}
