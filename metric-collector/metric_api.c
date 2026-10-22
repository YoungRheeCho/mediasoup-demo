#include<stdio.h>


void collect() {




} // 지표를 수집하는 함수

void send_LBmessage() {


} // 로드밸런싱을 위해 메트릭 콜렉터에서 메세지를 보내는 함수
// node information, performance information

// 기본적인 로직은 1초 주기로 데이터를 수집하여, 콜렉터에게 전송

// 콜렉터는 이를 기반으로 노드를 선택
// 지금 우선적으로 구현해야 할 것은 규칙 기반(휴리스틱) 초기 컨트롤 => not use db, and model

