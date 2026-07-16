"""
OCR 서비스 - 스캔 이미지 PDF 한국어 텍스트 추출
- pdf2image로 PDF 페이지를 이미지로 변환
- pytesseract(kor+eng)로 OCR 처리
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def is_scanned_pdf(filepath: str) -> bool:
    """PDF가 스캔 이미지 기반인지 판별 (텍스트 레이어 없음 = 스캔본)"""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(filepath)
        text_pages = 0
        check_pages = min(5, len(doc))  # 앞 5페이지만 확인

        for i in range(check_pages):
            text = doc[i].get_text().strip()
            if len(text) > 30:  # 30자 이상 텍스트 있으면 텍스트 PDF
                text_pages += 1

        doc.close()
        return text_pages == 0  # 텍스트 페이지가 0 -> 스캔본
    except Exception as e:
        logger.warning(f'is_scanned_pdf 판별 실패: {e}')
        return False


def ocr_pdf(filepath: str, dpi: int = 200) -> dict:
    """
    스캔 PDF -> OCR -> 페이지별 텍스트 및 평균 신뢰도 점수 반환
    
    Returns:
        {
            'pages': [{'page_number': int, 'text': str, 'confidence': float}, ...],
            'average_confidence': float, # 전체 페이지 평균 OCR 신뢰도 (0~100)
            'is_low_quality': bool       # 평균 신뢰도가 60 미만일 때 저품질로 판단
        }
    """
    try:
        from pdf2image import convert_from_path
        import pytesseract
        from pytesseract import Output

        logger.info(f'OCR 시작: {Path(filepath).name} (dpi={dpi})')

        # PDF -> 이미지 변환
        images = convert_from_path(
            filepath,
            dpi=dpi,
            fmt='jpeg',
            thread_count=2,
        )

        pages = []
        all_confidences = []
        
        for page_num, image in enumerate(images, 1):
            try:
                # pytesseract image_to_data를 이용한 신뢰도 추출
                data = pytesseract.image_to_data(
                    image,
                    lang='kor+eng',
                    config='--oem 3 --psm 6',
                    output_type=Output.DICT
                )
                
                # 단어별 텍스트 추출 및 신뢰도 수집
                words = []
                confidences = []
                for j in range(len(data['text'])):
                    word = data['text'][j].strip()
                    conf = float(data['conf'][j])
                    if word and conf != -1:
                        words.append(word)
                        confidences.append(conf)
                
                text = " ".join(words).strip()
                page_conf = sum(confidences) / len(confidences) if confidences else 0.0
                
                if text:
                    pages.append({
                        'page_number': page_num,
                        'text': text,
                        'confidence': page_conf,
                    })
                    all_confidences.extend(confidences)
                    logger.debug(f'  p{page_num}: {len(text)}자 추출 (신뢰도: {page_conf:.2f}%)')
            except Exception as e:
                logger.warning(f'  p{page_num} OCR 실패: {e}')

        avg_conf = sum(all_confidences) / len(all_confidences) if all_confidences else 0.0
        is_low_quality = avg_conf < 60.0 if all_confidences else True

        logger.info(f'OCR 완료: {len(pages)}/{len(images)} 페이지 텍스트 추출, 평균 신뢰도: {avg_conf:.2f}% (저품질 여부: {is_low_quality})')
        return {
            'pages': pages,
            'average_confidence': avg_conf,
            'is_low_quality': is_low_quality
        }

    except ImportError as e:
        logger.error(f'OCR 패키지 없음: {e}. pdf2image/pytesseract 설치 필요')
        return {'pages': [], 'average_confidence': 0.0, 'is_low_quality': True}
    except Exception as e:
        logger.error(f'OCR 처리 실패 ({filepath}): {e}')
        return {'pages': [], 'average_confidence': 0.0, 'is_low_quality': True}
