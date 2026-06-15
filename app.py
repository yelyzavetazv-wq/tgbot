def publish_to_channel(chat_id):
    try:
        bot.send_message(chat_id, "🔍 Шаг 1/11: Получение данных пользователя...")
        
        data = user_data.get(chat_id)
        choices = user_choices.get(chat_id)

        if not data or not choices:
            bot.send_message(chat_id, "❌ Ошибка: данные не найдены")
            return

        bot.send_message(chat_id, "🔍 Шаг 2/11: Проверка наличия EPUB и TXT файлов...")
        
        epub_path = data.get("epub")
        fb2_path = data.get("fb2")
        doc_path = data.get("doc")
        txt = data.get("txt", "")
        cover = data.get("cover")

        # EPUB и TXT обязательны
        if not epub_path:
            bot.send_message(chat_id, "❌ Ошибка: отсутствует EPUB файл")
            return
        if not txt:
            bot.send_message(chat_id, "❌ Ошибка: отсутствует TXT файл с описанием")
            return
        
        bot.send_message(chat_id, "✅ EPUB и TXT найдены")

        bot.send_message(chat_id, "🔍 Шаг 3/11: Парсинг информации из TXT и EPUB...")
        
        info = parse_info(txt, epub_path)
        
        if info.get('title_ru'):
            bot.send_message(chat_id, f"✅ Название: {info.get('title_ru')[:50]}...")
        else:
            bot.send_message(chat_id, f"⚠️ Название не найдено, используется 'Без названия'")

        bot.send_message(chat_id, "🔍 Шаг 4/11: Подсчёт количества глав...")
        
        chapters = count_chapters(epub_path)
        bot.send_message(chat_id, f"✅ Глав: {chapters}")

        bot.send_message(chat_id, "🔍 Шаг 5/11: Извлечение аннотации...")
        
        annotation = extract_annotation(epub_path)
        bot.send_message(chat_id, f"✅ Аннотация: {len(annotation)} символов")

        bot.send_message(chat_id, "🔍 Шаг 6/11: Получение выбранных параметров...")
        
        glossary = choices.get("glossary", "?")
        translation = choices.get("translation", "?")
        filter_choice = choices.get("filter", "none")
        status = choices.get("status", "?")
        
        bot.send_message(chat_id, f"✅ Глоссарий: {glossary[:30]}...")
        bot.send_message(chat_id, f"✅ Перевод: {translation[:30]}...")
        bot.send_message(chat_id, f"✅ Статус: {status}")

        bot.send_message(chat_id, "🔍 Шаг 7/11: Форматирование постов...")
        
        post2 = format_text(info, chapters, status, annotation)
        post3 = format_files(glossary, translation, filter_choice)
        bot.send_message(chat_id, f"✅ Посты сформированы (текст: {len(post2)} символов)")

        bot.send_message(chat_id, "🔍 Шаг 8/11: Отправка обложки...")
        
        if cover:
            with open(cover, "rb") as img:
                bot.send_photo(CHANNEL_ID, img, timeout=60)
            bot.send_message(chat_id, "✅ Обложка отправлена")
        else:
            bot.send_message(chat_id, "⚠️ Обложка не найдена")

        bot.send_message(chat_id, "🔍 Шаг 9/11: Отправка текста...")
        
        bot.send_message(
            CHANNEL_ID,
            post2,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        bot.send_message(chat_id, "✅ Текст отправлен")

        bot.send_message(chat_id, "🔍 Шаг 10/11: Отправка файлов...")
        
        # EPUB с подписью
        clean_name = f"{info.get('title_ru', 'book')}.epub"
        temp_epub = f"/tmp/{clean_name}"
        shutil.copy2(epub_path, temp_epub)

        with open(temp_epub, "rb") as f:
            bot.send_document(
                CHANNEL_ID,
                f,
                caption=post3,
                timeout=180
            )
        bot.send_message(chat_id, "✅ EPUB отправлен")

        if os.path.exists(temp_epub):
            os.remove(temp_epub)

        # Дополнительные файлы (FB2, DOC)
        if fb2_path:
            clean_name = f"{info.get('title_ru', 'book')}.fb2"
            temp_fb2 = f"/tmp/{clean_name}"
            shutil.copy2(fb2_path, temp_fb2)

            with open(temp_fb2, "rb") as f:
                bot.send_document(
                    CHANNEL_ID,
                    f,
                    timeout=180
                )
            bot.send_message(chat_id, "✅ FB2 отправлен")

            if os.path.exists(temp_fb2):
                os.remove(temp_fb2)

        if doc_path:
            clean_name = f"{info.get('title_ru', 'document')}.docx"
            temp_doc = f"/tmp/{clean_name}"
            shutil.copy2(doc_path, temp_doc)

            with open(temp_doc, "rb") as f:
                bot.send_document(
                    CHANNEL_ID,
                    f,
                    timeout=180
                )
            bot.send_message(chat_id, "✅ DOC отправлен")

            if os.path.exists(temp_doc):
                os.remove(temp_doc)

        bot.send_message(chat_id, "🔍 Шаг 11/11: Завершение и очистка...")
        
        bot.send_message(
            chat_id,
            f"✅ Книга '{info.get('title_ru', 'Без названия')}' опубликована в канале!"
        )

    except Exception as e:
        print(f"Ошибка публикации: {e}")
        bot.send_message(
            chat_id,
            f"❌ Ошибка публикации на шаге:\n{e}"
        )

    finally:
        cleanup_user(chat_id)
# В самом конце файла
application = app
